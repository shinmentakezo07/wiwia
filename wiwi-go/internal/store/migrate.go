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
		`ALTER TABLE request_logs ADD COLUMN model_id TEXT DEFAULT ''`,
		`ALTER TABLE deployments ADD COLUMN extra_config TEXT DEFAULT ''`,
	}
	if s.driver == DriverPostgres {
		alters = []string{
			`ALTER TABLE request_logs ADD COLUMN IF NOT EXISTS model_id TEXT DEFAULT ''`,
			`ALTER TABLE deployments ADD COLUMN IF NOT EXISTS extra_config TEXT DEFAULT ''`,
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
	// The schema is a copy of the Python backend's DDL (wiwi/ is canonical),
	// so both backends can share one database via the same DATABASE_URL.
	// Where Python distinguishes dialects only in the auto-increment keyword
	// (SQLite AUTOINCREMENT, Postgres SERIAL) or the float type (REAL vs
	// DOUBLE PRECISION), the choice is made here from the active driver.
	autoID := "INTEGER PRIMARY KEY AUTOINCREMENT"
	if s.driver == DriverPostgres {
		autoID = "SERIAL PRIMARY KEY"
	}
	f := s.realType()
	return []string{
		// wiwi/auth/service.py CREATE_SQL. Python spells every epoch/money
		// column DOUBLE PRECISION, which is s.realType() on Postgres and an
		// equally valid 8-byte float on SQLite.
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS vkeys (
			id TEXT PRIMARY KEY,
			key_hash TEXT UNIQUE NOT NULL,
			key_alias TEXT NOT NULL DEFAULT '',
			models TEXT NOT NULL DEFAULT '[]',
			max_budget %[2]s,
			spend_to_date %[2]s NOT NULL DEFAULT 0,
			budget_reserved %[2]s NOT NULL DEFAULT 0,
			reserved_at %[2]s NOT NULL DEFAULT 0,
			reservation_owner TEXT,
			rpm INTEGER,
			tpm INTEGER,
			expires_at %[2]s,
			disabled INTEGER NOT NULL DEFAULT 0,
			owner_id TEXT,
			priority TEXT,
			created_at %[2]s NOT NULL,
			updated_at %[2]s NOT NULL
		)`, f, f),

		// wiwi/auth/users.py USERS_DDL
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS users (
			id TEXT PRIMARY KEY,
			username TEXT UNIQUE NOT NULL,
			password_hash TEXT NOT NULL,
			role TEXT NOT NULL DEFAULT 'user',
			disabled INTEGER NOT NULL DEFAULT 0,
			created_at %[1]s NOT NULL,
			updated_at %[1]s NOT NULL
		)`, f),

		// Go-only session store; same shape as Python's but with float epoch
		// columns, matching how Python writes its timestamps.
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS sessions (
			id TEXT PRIMARY KEY,
			user_id TEXT,
			created_at %[1]s,
			expires_at %[1]s
		)`, f),

		// wiwi/logging_core/db_sink.py _REQUEST_DDL_*
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS request_logs (
			id %[2]s,
			ts %[2]s NOT NULL,
			request_id TEXT DEFAULT '',
			surface TEXT DEFAULT '',
			key_alias TEXT DEFAULT '',
			key_id TEXT DEFAULT '',
			model_group TEXT DEFAULT '',
			provider TEXT DEFAULT '',
			provider_key_label TEXT DEFAULT '',
			status INTEGER DEFAULT 200,
			error_code TEXT DEFAULT '',
			error_message TEXT DEFAULT '',
			tok_in INTEGER DEFAULT 0,
			tok_cached INTEGER DEFAULT 0,
			tok_cache_creation INTEGER DEFAULT 0,
			tok_reasoning INTEGER DEFAULT 0,
			tok_out INTEGER DEFAULT 0,
			usage_estimated INTEGER DEFAULT 0,
			tps %[2]s DEFAULT 0,
			ttft_ms %[2]s DEFAULT 0,
			latency_ms %[2]s DEFAULT 0,
			cost %[2]s DEFAULT 0,
			was_stream INTEGER DEFAULT 0,
			cache_hit INTEGER DEFAULT 0,
			cache_savings %[2]s DEFAULT 0,
			response_cache_hit INTEGER DEFAULT 0,
			attempts TEXT DEFAULT '[]',
			translation_warnings TEXT DEFAULT '[]',
			request_body TEXT,
			response_body TEXT
		)`, autoID, f),

		// wiwi/logging_core/db_sink.py _AUDIT_DDL_*
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS audit_logs (
			id %[2]s,
			ts %[2]s NOT NULL,
			actor TEXT DEFAULT '',
			action TEXT DEFAULT '',
			target TEXT DEFAULT '',
			diff TEXT DEFAULT '{}'
		)`, autoID, f),

		// wiwi/logging_core/db_sink.py _ROLLUP_DDL_*
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS request_rollups (
			id %[2]s,
			bucket_ts %[2]s NOT NULL,
			key_id TEXT DEFAULT '',
			model_group TEXT DEFAULT '',
			provider TEXT DEFAULT '',
			serving_model TEXT DEFAULT '',
			requests INTEGER DEFAULT 0,
			errors INTEGER DEFAULT 0,
			estimated_requests INTEGER DEFAULT 0,
			cache_hits INTEGER DEFAULT 0,
			tok_in INTEGER DEFAULT 0,
			tok_cached INTEGER DEFAULT 0,
			tok_cache_creation INTEGER DEFAULT 0,
			tok_reasoning INTEGER DEFAULT 0,
			tok_out INTEGER DEFAULT 0,
			cost %[2]s DEFAULT 0,
			cache_savings %[2]s DEFAULT 0,
			tps_sum %[2]s DEFAULT 0,
			tps_count INTEGER DEFAULT 0,
			tps_p95 %[2]s DEFAULT 0,
			ttft_p95_ms %[2]s DEFAULT 0,
			latency_p95_ms %[2]s DEFAULT 0,
			p95_hist TEXT DEFAULT '',
			unpriced_requests INTEGER DEFAULT 0,
			unpriced_tok_in INTEGER DEFAULT 0,
			unpriced_tok_cached INTEGER DEFAULT 0,
			unpriced_tok_cache_creation INTEGER DEFAULT 0,
			unpriced_tok_out INTEGER DEFAULT 0
		)`, autoID, f),

		// wiwi/logging_core/db_sink.py _SWEEP_LOCK_DDL_*
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS sweep_locks (
			name TEXT PRIMARY KEY,
			ts %[1]s NOT NULL
		)`, f),

		// wiwi/server/config_store.py PROVIDER_DDL
		`CREATE TABLE IF NOT EXISTS providers (
			name TEXT PRIMARY KEY,
			provider_type TEXT NOT NULL,
			base_url TEXT NOT NULL DEFAULT '',
			timeout_s DOUBLE PRECISION NOT NULL DEFAULT 120.0,
			extra_headers TEXT NOT NULL DEFAULT '{}',
			round_robin INTEGER NOT NULL DEFAULT 1,
			enabled INTEGER NOT NULL DEFAULT 1,
			alias_id TEXT,
			detached INTEGER NOT NULL DEFAULT 0
		)`,

		// wiwi/server/config_store.py KEY_DDL_*
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS provider_keys (
			id %[1]s,
			provider_name TEXT NOT NULL,
			label TEXT NOT NULL,
			secret TEXT NOT NULL,
			weight INTEGER NOT NULL DEFAULT 1,
			enabled INTEGER NOT NULL DEFAULT 1,
			detached INTEGER NOT NULL DEFAULT 0,
			UNIQUE(provider_name, label)
		)`, autoID),

		// wiwi/server/config_store.py DEPLOYMENT_DDL_*
		fmt.Sprintf(`CREATE TABLE IF NOT EXISTS deployments (
			id %[2]s,
			group_name TEXT NOT NULL,
			provider_name TEXT NOT NULL,
			model_id TEXT NOT NULL,
			weight INTEGER NOT NULL DEFAULT 1,
			detached INTEGER NOT NULL DEFAULT 0,
			rpm INTEGER,
			tpm INTEGER,
			timeout %[2]s,
			max_tokens INTEGER,
			prompt_cache INTEGER,
			UNIQUE(group_name, provider_name, model_id)
		)`, autoID, f),

		// wiwi/server/config_store.py SETTINGS_DDL
		`CREATE TABLE IF NOT EXISTS settings (
			key TEXT PRIMARY KEY,
			value TEXT NOT NULL
		)`,

		// wiwi/server/config_store.py MODEL_PRICES_DDL
		`CREATE TABLE IF NOT EXISTS model_prices (
			model_id TEXT PRIMARY KEY,
			input_cost_per_token DOUBLE PRECISION NOT NULL DEFAULT 0,
			output_cost_per_token DOUBLE PRECISION NOT NULL DEFAULT 0,
			cache_read_input_cost_per_token DOUBLE PRECISION,
			cache_creation_input_cost_per_token DOUBLE PRECISION,
			max_input_tokens INTEGER,
			max_output_tokens INTEGER,
			mode TEXT
		)`,

		// wiwi/server/config_store.py MODEL_PRICE_SCOPES_DDL
		`CREATE TABLE IF NOT EXISTS model_price_scopes (
			model_id TEXT NOT NULL,
			scope TEXT NOT NULL,
			input_cost_per_token DOUBLE PRECISION NOT NULL DEFAULT 0,
			output_cost_per_token DOUBLE PRECISION NOT NULL DEFAULT 0,
			cache_read_input_cost_per_token DOUBLE PRECISION,
			cache_creation_input_cost_per_token DOUBLE PRECISION,
			PRIMARY KEY (model_id, scope)
		)`,

		// wiwi/server/response_store.py RESPONSE_DDL
		`CREATE TABLE IF NOT EXISTS stored_responses (
			id TEXT PRIMARY KEY,
			created_at DOUBLE PRECISION NOT NULL,
			key_id TEXT NOT NULL,
			surface TEXT NOT NULL DEFAULT 'responses',
			model_group TEXT NOT NULL DEFAULT '',
			input_json TEXT NOT NULL,
			output_json TEXT NOT NULL
		)`,

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
