package store

import (
	"database/sql"
	"path/filepath"
	"sort"
	"testing"
)

func TestNormalize(t *testing.T) {
	cases := []struct {
		in     string
		driver string
		dsn    string
		memory bool
	}{
		{"", DriverSQLite, "wiwi.db", false},
		{"wiwi.db", DriverSQLite, "wiwi.db", false},
		// Three slashes = relative (SQLAlchemy semantics), four = absolute.
		// Getting this wrong made the default resolve to the unwritable
		// "/wiwi.db" and the server refused to start.
		{"sqlite+aiosqlite:///wiwi.db", DriverSQLite, "wiwi.db", false},
		{"sqlite+aiosqlite:///tmp/a.db", DriverSQLite, "tmp/a.db", false},
		{"sqlite:///tmp/a.db", DriverSQLite, "tmp/a.db", false},
		{"sqlite:////tmp/a.db", DriverSQLite, "/tmp/a.db", false},
		{"sqlite://rel/a.db", DriverSQLite, "rel/a.db", false},
		{":memory:", DriverSQLite, "file::memory:?cache=shared", true},
		{"sqlite:///", DriverSQLite, "file::memory:?cache=shared", true},
		{"postgres://u:p@h:5432/db", DriverPostgres, "postgres://u:p@h:5432/db", false},
		{"postgresql://u:p@h/db", DriverPostgres, "postgresql://u:p@h/db", false},
		{"postgresql+asyncpg://u:p@h/db", DriverPostgres, "postgres://u:p@h/db", false},
	}
	for _, c := range cases {
		driver, dsn, memory := normalize(c.in)
		if driver != c.driver || dsn != c.dsn || memory != c.memory {
			t.Errorf("normalize(%q) = (%q, %q, %v), want (%q, %q, %v)",
				c.in, driver, dsn, memory, c.driver, c.dsn, c.memory)
		}
	}
}

func TestSQLiteDSNAddsPragmas(t *testing.T) {
	if got := sqliteDSN("a.db", false); got != "a.db?_journal_mode=WAL&_busy_timeout=5000&_foreign_keys=on" {
		t.Errorf("file DSN = %q", got)
	}
	if got := sqliteDSN("file::memory:?cache=shared", true); got != "file::memory:?cache=shared&_busy_timeout=5000&_foreign_keys=on" {
		t.Errorf("memory DSN = %q", got)
	}
}

func TestOpenMigrateSQLiteFile(t *testing.T) {
	path := filepath.Join(t.TempDir(), "wiwi.db")
	s, err := Open("sqlite:///" + path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer s.Close()

	if got := s.Driver(); got != DriverSQLite {
		t.Fatalf("Driver() = %q, want %q", got, DriverSQLite)
	}
	if s.DB() == nil {
		t.Fatal("DB() = nil")
	}

	// Migrate must be idempotent.
	if err := s.Migrate(); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	if err := s.Migrate(); err != nil {
		t.Fatalf("Migrate (second call): %v", err)
	}

	var journal string
	if err := s.QueryRow("PRAGMA journal_mode").Scan(&journal); err != nil {
		t.Fatalf("PRAGMA journal_mode: %v", err)
	}
	if journal != "wal" {
		t.Errorf("journal_mode = %q, want wal", journal)
	}
	var fk int
	if err := s.QueryRow("PRAGMA foreign_keys").Scan(&fk); err != nil {
		t.Fatalf("PRAGMA foreign_keys: %v", err)
	}
	if fk != 1 {
		t.Errorf("foreign_keys = %d, want 1", fk)
	}
	var busy int
	if err := s.QueryRow("PRAGMA busy_timeout").Scan(&busy); err != nil {
		t.Fatalf("PRAGMA busy_timeout: %v", err)
	}
	if busy != 5000 {
		t.Errorf("busy_timeout = %d, want 5000", busy)
	}
	want := []string{
		"audit_logs", "deployments", "model_price_scopes", "model_prices",
		"provider_keys", "providers", "request_logs", "request_rollups",
		"responses", "sessions", "settings", "stored_responses",
		"sweep_locks", "users", "vkeys",
	}
	var got []string
	rows, err := s.Query(`SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'`)
	if err != nil {
		t.Fatalf("list tables: %v", err)
	}
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			t.Fatalf("scan table: %v", err)
		}
		got = append(got, name)
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("list tables: %v", err)
	}
	rows.Close()
	sort.Strings(got)
	if len(got) != len(want) {
		t.Fatalf("tables = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("tables = %v, want %v", got, want)
		}
	}

	var indexes int
	if err := s.QueryRow(
		`SELECT count(*) FROM sqlite_master WHERE type = 'index' AND name IN ('idx_request_logs_ts','idx_vkeys_key_hash')`,
	).Scan(&indexes); err != nil {
		t.Fatalf("count indexes: %v", err)
	}
	if indexes != 2 {
		t.Errorf("found %d of 2 expected indexes", indexes)
	}
}

func TestSettingsRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "settings.db")
	s, err := Open("sqlite:///" + path)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if err := s.Migrate(); err != nil {
		t.Fatalf("Migrate: %v", err)
	}

	if _, ok, err := s.GetSetting("missing"); err != nil || ok {
		t.Fatalf("GetSetting(missing) = ok=%v err=%v, want false nil", ok, err)
	}

	if err := s.SetSetting("master_key", "sk-test"); err != nil {
		t.Fatalf("SetSetting: %v", err)
	}
	got, ok, err := s.GetSetting("master_key")
	if err != nil || !ok || got != "sk-test" {
		t.Fatalf("GetSetting = (%q, %v, %v), want (sk-test, true, nil)", got, ok, err)
	}

	// Upsert, not duplicate insert.
	if err := s.SetSetting("master_key", "sk-other"); err != nil {
		t.Fatalf("SetSetting (overwrite): %v", err)
	}
	got, _, err = s.GetSetting("master_key")
	if err != nil || got != "sk-other" {
		t.Fatalf("GetSetting after overwrite = (%q, %v)", got, err)
	}

	if err := s.SetSetting("empty", ""); err != nil {
		t.Fatalf("SetSetting(empty): %v", err)
	}
	if got, ok, err := s.GetSetting("empty"); err != nil || !ok || got != "" {
		t.Fatalf("GetSetting(empty) = (%q, %v, %v)", got, ok, err)
	}

	all, err := s.ListSettings()
	if err != nil {
		t.Fatalf("ListSettings: %v", err)
	}
	if len(all) != 2 || all["master_key"] != "sk-other" || all["empty"] != "" {
		t.Fatalf("ListSettings = %v", all)
	}

	// Values must survive a close/reopen of the same file.
	if err := s.Close(); err != nil {
		t.Fatalf("Close: %v", err)
	}
	s2, err := Open("sqlite:///" + path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	defer s2.Close()
	if got, ok, err := s2.GetSetting("master_key"); err != nil || !ok || got != "sk-other" {
		t.Fatalf("GetSetting after reopen = (%q, %v, %v)", got, ok, err)
	}
}

func TestMemoryStore(t *testing.T) {
	s, err := Open(":memory:")
	if err != nil {
		t.Fatalf("Open(:memory:): %v", err)
	}
	defer s.Close()

	if err := s.Migrate(); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	if err := s.SetSetting("k", "v"); err != nil {
		t.Fatalf("SetSetting: %v", err)
	}
	if got, ok, err := s.GetSetting("k"); err != nil || !ok || got != "v" {
		t.Fatalf("GetSetting = (%q, %v, %v)", got, ok, err)
	}
}

func TestRebind(t *testing.T) {
	pg := &Store{driver: DriverPostgres}
	if got := pg.Rebind("SELECT a FROM t WHERE b = ? AND c = ?"); got != "SELECT a FROM t WHERE b = $1 AND c = $2" {
		t.Errorf("postgres rebind = %q", got)
	}
	// Placeholders inside string literals must not be rewritten.
	if got := pg.Rebind("SELECT '?' AS q, \"we?ird\" FROM t WHERE a = ?"); got != "SELECT '?' AS q, \"we?ird\" FROM t WHERE a = $1" {
		t.Errorf("quoted rebind = %q", got)
	}
	if got := pg.placeholder(3); got != "$3" {
		t.Errorf("placeholder(3) = %q", got)
	}

	sq := &Store{driver: DriverSQLite}
	if got := sq.Rebind("SELECT a FROM t WHERE b = ?"); got != "SELECT a FROM t WHERE b = ?" {
		t.Errorf("sqlite rebind = %q", got)
	}
	if got := sq.placeholder(3); got != "?" {
		t.Errorf("sqlite placeholder(3) = %q", got)
	}
}

func TestExecQueryQueryRow(t *testing.T) {
	s, err := Open(":memory:")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer s.Close()
	if err := s.Migrate(); err != nil {
		t.Fatalf("Migrate: %v", err)
	}
	if err := s.Exec(`INSERT INTO model_prices(model_id, input_cost_per_token, output_cost_per_token, cache_read_input_cost_per_token) VALUES(?, ?, ?, ?)`,
		"gpt-4o", 0.0000025, 0.00001, 0.00000125); err != nil {
		t.Fatalf("Exec: %v", err)
	}

	var completion float64
	if err := s.QueryRow(`SELECT output_cost_per_token FROM model_prices WHERE model_id = ?`, "gpt-4o").Scan(&completion); err != nil {
		t.Fatalf("QueryRow: %v", err)
	}
	if completion != 0.00001 {
		t.Errorf("completion = %v, want 0.00001", completion)
	}

	if err := s.QueryRow(`SELECT output_cost_per_token FROM model_prices WHERE model_id = ?`, "nope").Scan(&completion); err != sql.ErrNoRows {
		t.Errorf("QueryRow(missing) err = %v, want sql.ErrNoRows", err)
	}

	rows, err := s.Query(`SELECT model_id FROM model_prices`)
	if err != nil {
		t.Fatalf("Query: %v", err)
	}
	defer rows.Close()
	n := 0
	for rows.Next() {
		n++
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("rows.Err: %v", err)
	}
	if n != 1 {
		t.Errorf("row count = %d, want 1", n)
	}
}

func TestOpenMissingDirectory(t *testing.T) {
	path := filepath.Join(t.TempDir(), "no-such-dir", "wiwi.db")
	s, err := Open("sqlite:///" + path)
	if err == nil {
		s.Close()
		t.Fatal("Open on a missing directory returned no error")
	}
}
