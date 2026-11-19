package store

import "fmt"

// Migrate creates every table and index this gateway needs. It is idempotent
// (CREATE ... IF NOT EXISTS) and runs in a single transaction, so a failure
// leaves no half-built schema behind.
func (s *Store) Migrate() error {
	tx, err := s.db.Begin()
	if err != nil {
		return fmt.Errorf("store: migrate: begin: %w", err)
	}
	defer func() { _ = tx.Rollback() }()

	for _, stmt := range s.schema() {
		if _, err := tx.Exec(s.Rebind(stmt)); err != nil {
			return fmt.Errorf("store: migrate: %w", err)
		}
	}
	// Columns added after the initial schema. Postgres supports ADD COLUMN IF
	// NOT EXISTS; SQLite does not, so there the statement is best-effort and a
	// "duplicate column" error on an existing database is expected. Run after
	// the transaction commits: a failed ALTER would poison a Postgres tx.
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("store: migrate: commit: %w", err)
	}
	alters := []string{
		`ALTER TABLE users ADD COLUMN disabled INTEGER DEFAULT 0`,
		fmt.Sprintf(`ALTER TABLE request_logs ADD COLUMN tps %s`, s.realType()),
	}
	if s.driver == DriverPostgres {
		alters = []string{
			`ALTER TABLE users ADD COLUMN IF NOT EXISTS disabled INTEGER DEFAULT 0`,
			fmt.Sprintf(`ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS tps %s`, s.realType()),
		}
	}
	for _, alter := range alters {
		_, _ = s.db.Exec(alter)
	}
	return nil
}

// realType is the floating-point column type for the active driver. SQLite's
// REAL is already 8 bytes; Postgres' REAL is 4, so money must be DOUBLE
// PRECISION there.
func (s *Store) realType() string {
	if s.driver == DriverPostgres {
		return "DOUBLE PRECISION"
	}
	return "REAL"
}

func (s *Store) schema() []string {
	f := s.realType()
	return []string{
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS vkeys (
			id TEXT PRIMARY KEY,
			name TEXT,
			owner TEXT,
			key_hash TEXT UNIQUE,
			max_budget %[1]s,
			spend %[1]s,
			rpm INTEGER,
			tpm INTEGER,
			models TEXT,
			enabled INTEGER,
			expires_at INTEGER,
			created_at INTEGER,
			budget_reserved %[1]s,
			reserved_at INTEGER
		)`, f),

		`CREATE TABLE IF NOT EXISTS users (
			id TEXT PRIMARY KEY,
			username TEXT UNIQUE,
			pw_hash TEXT,
			role TEXT,
			created_at INTEGER
		)`,

		`CREATE TABLE IF NOT EXISTS sessions (
			id TEXT PRIMARY KEY,
			user_id TEXT,
			created_at INTEGER,
			expires_at INTEGER
		)`,

		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS request_logs (
			id TEXT PRIMARY KEY,
			request_id TEXT,
			ts INTEGER,
			key_id TEXT,
			model_group TEXT,
			provider TEXT,
			model_id TEXT,
			status INTEGER,
			prompt_tokens INTEGER,
			completion_tokens INTEGER,
			cached_tokens INTEGER,
			reasoning_tokens INTEGER,
			cost %[1]s,
			latency_ms INTEGER,
			ttft_ms INTEGER,
			tps %[1]s,
			error TEXT,
			attempts TEXT,
			prompt_text TEXT
		)`, f),

		`CREATE TABLE IF NOT EXISTS audit_logs (
			id TEXT PRIMARY KEY,
			ts INTEGER,
			actor TEXT,
			action TEXT,
			target TEXT,
			diff TEXT
		)`,

		`CREATE TABLE IF NOT EXISTS providers (
			name TEXT PRIMARY KEY,
			provider_type TEXT,
			base_url TEXT,
			config TEXT
		)`,

		`CREATE TABLE IF NOT EXISTS deployments (
			id TEXT PRIMARY KEY,
			model_name TEXT,
			provider TEXT,
			model TEXT,
			weight INTEGER,
			config TEXT
		)`,

		`CREATE TABLE IF NOT EXISTS settings (
			"key" TEXT PRIMARY KEY,
			value TEXT
		)`,

		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS model_prices (
			model TEXT PRIMARY KEY,
			prompt %[1]s,
			completion %[1]s,
			cached %[1]s
		)`, f),

		`CREATE TABLE IF NOT EXISTS responses (
			id TEXT PRIMARY KEY,
			key_id TEXT,
			body TEXT,
			created_at INTEGER,
			expires_at INTEGER
		)`,

		`CREATE INDEX IF NOT EXISTS idx_request_logs_ts ON request_logs(ts)`,
		`CREATE INDEX IF NOT EXISTS idx_vkeys_key_hash ON vkeys(key_hash)`,
	}
}
