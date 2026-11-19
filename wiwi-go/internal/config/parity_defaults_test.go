package config

import "testing"

func TestParityDefaults(t *testing.T) {
	cfg := &Config{}
	cfg.General.MasterKey = "sk-test"
	cfg.ApplyDefaults()

	if cfg.Wiwi.ResponseStoreSweepInterval != 300 {
		t.Fatalf("response_store_sweep_interval_s = %v, want 300", cfg.Wiwi.ResponseStoreSweepInterval)
	}
	if cfg.Wiwi.HTTPMaxConnections != 200 || cfg.Wiwi.HTTPMaxKeepalive != 40 {
		t.Fatalf("http pool defaults = %d/%d, want 200/40",
			cfg.Wiwi.HTTPMaxConnections, cfg.Wiwi.HTTPMaxKeepalive)
	}
	if cfg.Wiwi.HTTPKeepaliveExpiryS != 30 {
		t.Fatalf("http_keepalive_expiry_s = %v, want 30", cfg.Wiwi.HTTPKeepaliveExpiryS)
	}
	if cfg.Wiwi.SingleflightMaxFlights != 2048 || cfg.Wiwi.SingleflightMaxWaiters != 64 {
		t.Fatalf("singleflight defaults = %d/%d, want 2048/64",
			cfg.Wiwi.SingleflightMaxFlights, cfg.Wiwi.SingleflightMaxWaiters)
	}
	if cfg.Telemetry.ExportTimeoutS != 10 {
		t.Fatalf("export_timeout_s = %v, want 10", cfg.Telemetry.ExportTimeoutS)
	}
}

func TestParityConfigRejectsNegativeBounds(t *testing.T) {
	cases := []func(*Config){
		func(c *Config) { c.Wiwi.ResponseStoreSweepInterval = -1 },
		func(c *Config) { c.Wiwi.HTTPKeepaliveExpiryS = -1 },
		func(c *Config) { c.Wiwi.SingleflightMaxFlights = -1 },
		func(c *Config) { c.Wiwi.SingleflightMaxWaiters = -1 },
		func(c *Config) { c.Telemetry.ExportTimeoutS = -1 },
	}
	for i, mutate := range cases {
		cfg := &Config{}
		cfg.General.MasterKey = "sk-test"
		mutate(cfg)
		if err := cfg.Validate(); err == nil {
			t.Fatalf("case %d: expected a validation error", i)
		}
	}
}
