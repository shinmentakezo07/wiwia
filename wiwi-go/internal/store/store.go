// Package store is the only package that imports SQL drivers. It wraps
// database/sql for SQLite and Postgres behind one small API, normalizes the
// LiteLLM-style database URLs used by the config, and owns the schema.
package store

import (
	"database/sql"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	_ "github.com/lib/pq"
	_ "github.com/mattn/go-sqlite3"
)

// Driver names as reported by Driver(). These are logical dialects, not the
// database/sql driver names ("sqlite3", "postgres").
const (
	DriverSQLite   = "sqlite"
	DriverPostgres = "postgres"
)

const defaultSQLitePath = "wiwi.db"

// Store is a thin database/sql wrapper that hides the placeholder and type
// differences between SQLite and Postgres.
type Store struct {
	db     *sql.DB
	driver string
}

// Open connects to databaseURL. An empty URL means the default SQLite file
// "wiwi.db". Recognized shapes:
//
//	sqlite+aiosqlite:///path, sqlite:///path, sqlite://path  -> SQLite file
//	postgresql+asyncpg://...                                 -> postgres://
//	postgres://... , postgresql://...                        -> Postgres
//
// A bare path without a scheme is treated as a SQLite file.
func Open(databaseURL string) (*Store, error) {
	driver, dsn, memory := normalize(databaseURL)

	switch driver {
	case DriverSQLite:
		db, err := sql.Open("sqlite3", sqliteDSN(dsn, memory))
		if err != nil {
			return nil, fmt.Errorf("store: open sqlite: %w", err)
		}
		if memory {
			// A shared-cache in-memory database lives only while a
			// connection to it is open, so pin the pool to one connection.
			db.SetMaxOpenConns(1)
			db.SetMaxIdleConns(1)
		} else {
			// WAL allows concurrent readers plus one writer; the busy
			// timeout absorbs write contention.
			db.SetMaxOpenConns(4)
			db.SetMaxIdleConns(4)
		}
		if err := db.Ping(); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("store: open sqlite %q: %w", dsn, err)
		}
		s := &Store{db: db, driver: DriverSQLite}
		if err := s.applySQLitePragmas(); err != nil {
			_ = db.Close()
			return nil, err
		}
		return s, nil

	case DriverPostgres:
		db, err := sql.Open("postgres", dsn)
		if err != nil {
			return nil, fmt.Errorf("store: open postgres: %w", err)
		}
		db.SetMaxOpenConns(10)
		db.SetMaxIdleConns(5)
		if err := db.Ping(); err != nil {
			_ = db.Close()
			return nil, fmt.Errorf("store: open postgres: %w", err)
		}
		return &Store{db: db, driver: DriverPostgres}, nil
	}

	return nil, fmt.Errorf("store: unsupported database driver %q", driver)
}

// normalize maps a configured URL to (logical driver, DSN, in-memory flag).
func normalize(raw string) (driver, dsn string, memory bool) {
	url := strings.TrimSpace(raw)
	if url == "" {
		return DriverSQLite, defaultSQLitePath, false
	}

	switch {
	case strings.HasPrefix(url, "sqlite+aiosqlite://"):
		url = strings.TrimPrefix(url, "sqlite+aiosqlite://")
	case strings.HasPrefix(url, "sqlite3://"):
		url = strings.TrimPrefix(url, "sqlite3://")
	case strings.HasPrefix(url, "sqlite://"):
		url = strings.TrimPrefix(url, "sqlite://")
	case strings.HasPrefix(url, "postgresql+asyncpg://"):
		return DriverPostgres, "postgres://" + strings.TrimPrefix(url, "postgresql+asyncpg://"), false
	case strings.HasPrefix(url, "postgres://"), strings.HasPrefix(url, "postgresql://"):
		return DriverPostgres, url, false
	}

	// Everything else is a SQLite file path (including "wiwi.db"). An empty
	// path ("sqlite:///" or ":memory:") means an in-memory database.
	if url == "" || url == "/" || url == ":memory:" || strings.HasPrefix(url, "file::memory:") {
		return DriverSQLite, "file::memory:?cache=shared", true
	}
	return DriverSQLite, url, false
}

// sqliteDSN adds per-connection pragmas to a go-sqlite3 DSN. DSN parameters
// are the only way to guarantee every pooled connection gets them.
func sqliteDSN(path string, memory bool) string {
	sep := "?"
	if strings.Contains(path, "?") {
		sep = "&"
	}
	params := "_busy_timeout=5000&_foreign_keys=on"
	if !memory {
		params = "_journal_mode=WAL&" + params
	}
	return path + sep + params
}

// applySQLitePragmas sets the pragmas on the initial connection as well, so a
// caller that grabs that connection directly sees the same settings.
func (s *Store) applySQLitePragmas() error {
	if _, err := s.db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		return fmt.Errorf("store: sqlite pragma journal_mode: %w", err)
	}
	if _, err := s.db.Exec("PRAGMA foreign_keys=ON"); err != nil {
		return fmt.Errorf("store: sqlite pragma foreign_keys: %w", err)
	}
	if _, err := s.db.Exec("PRAGMA busy_timeout=5000"); err != nil {
		return fmt.Errorf("store: sqlite pragma busy_timeout: %w", err)
	}
	return nil
}

// DB exposes the underlying pool for callers that need transactions.
func (s *Store) DB() *sql.DB { return s.db }

// Driver reports the logical dialect: "sqlite" or "postgres".
func (s *Store) Driver() string { return s.driver }

// Close releases the connection pool.
func (s *Store) Close() error {
	if s.db == nil {
		return nil
	}
	return s.db.Close()
}

// placeholder renders the i-th (1-based) bind parameter for the driver.
func (s *Store) placeholder(i int) string {
	if s.driver == DriverPostgres {
		return "$" + strconv.Itoa(i)
	}
	return "?"
}

// Rebind converts '?' placeholders to $1..$n for Postgres. Quoted string
// literals and identifiers are left untouched, so a '?' inside them survives.
// SQLite queries are returned unchanged.
func (s *Store) Rebind(q string) string {
	if s.driver != DriverPostgres {
		return q
	}
	var b strings.Builder
	b.Grow(len(q) + 8)
	n := 0
	inSingle, inDouble := false, false
	for i := 0; i < len(q); i++ {
		c := q[i]
		switch {
		case c == '\'' && !inDouble:
			inSingle = !inSingle
			b.WriteByte(c)
		case c == '"' && !inSingle:
			inDouble = !inDouble
			b.WriteByte(c)
		case c == '?' && !inSingle && !inDouble:
			n++
			b.WriteString(s.placeholder(n))
		default:
			b.WriteByte(c)
		}
	}
	return b.String()
}

// Exec runs a statement, rebinding placeholders for Postgres.
func (s *Store) Exec(q string, args ...any) error {
	_, err := s.db.Exec(s.Rebind(q), args...)
	if err != nil {
		return fmt.Errorf("store: exec: %w", err)
	}
	return nil
}

// Query runs a query, rebinding placeholders for Postgres.
func (s *Store) Query(q string, args ...any) (*sql.Rows, error) {
	rows, err := s.db.Query(s.Rebind(q), args...)
	if err != nil {
		return nil, fmt.Errorf("store: query: %w", err)
	}
	return rows, nil
}

// QueryRow runs a single-row query, rebinding placeholders for Postgres.
func (s *Store) QueryRow(q string, args ...any) *sql.Row {
	return s.db.QueryRow(s.Rebind(q), args...)
}

// GetSetting returns the stored value and whether the key exists.
func (s *Store) GetSetting(key string) (string, bool, error) {
	var value string
	err := s.QueryRow(`SELECT value FROM settings WHERE "key" = ?`, key).Scan(&value)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	if err != nil {
		return "", false, fmt.Errorf("store: get setting %q: %w", key, err)
	}
	return value, true, nil
}

// SetSetting inserts or replaces a setting.
func (s *Store) SetSetting(key, value string) error {
	err := s.Exec(
		`INSERT INTO settings("key", value) VALUES(?, ?)
		 ON CONFLICT("key") DO UPDATE SET value = EXCLUDED.value`,
		key, value,
	)
	if err != nil {
		return fmt.Errorf("store: set setting %q: %w", key, err)
	}
	return nil
}

// ListSettings returns every setting as a map.
func (s *Store) ListSettings() (map[string]string, error) {
	rows, err := s.Query(`SELECT "key", value FROM settings`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make(map[string]string)
	for rows.Next() {
		var key, value string
		if err := rows.Scan(&key, &value); err != nil {
			return nil, fmt.Errorf("store: list settings: %w", err)
		}
		out[key] = value
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("store: list settings: %w", err)
	}
	return out, nil
}

// ConfigurePool applies connection-pool limits (Postgres; SQLite ignores most
// of this). Call after Open.
func (s *Store) ConfigurePool(maxOpen, maxIdle int, recycle time.Duration) {
	if s == nil || s.db == nil {
		return
	}
	if maxOpen > 0 {
		s.db.SetMaxOpenConns(maxOpen)
	}
	if maxIdle > 0 {
		s.db.SetMaxIdleConns(maxIdle)
	}
	if recycle > 0 {
		s.db.SetConnMaxLifetime(recycle)
	}
}
