package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const sampleYAML = `
providers:
  - name: openai-main
    provider: openai
    base_url: https://api.openai.com/v1
    timeout_s: 90
    extra_headers:
      X-Title: wiwi
    round_robin: false
    keys:
      - {label: main, key: sk-main, weight: 3}
      - {label: backup, key: sk-backup}
  - name: anthropic-main
    provider: anthropic
    keys:
      - {label: main, key: sk-ant}
    alias_id: anth
model_list:
  - model_name: my-gpt
    wiwi_params: {provider: openai-main, model: gpt-4o, weight: 2, max_tokens: 4096, rpm: 500}
  - model_name: flat
    provider: anthropic-main
    model: claude-3-5-sonnet
router_settings:
  routing_strategy: least-busy
  num_retries: 4
  timeout: 60
  stream_loop_detection: false
  fallbacks:
    my-gpt: [flat]
  model_group_alias:
    gpt-4: my-gpt
    fancy: {target: my-gpt, force_mapping: false, display_name: Fancy}
general_settings:
  master_key: sk-test
  database_url: postgres://user:pw@db/wiwi
  trusted_proxies: "127.0.0.1/32, 10.0.0.0/8"
wiwi_settings:
  host: 127.0.0.1
  port: 8080
cache_settings:
  enabled: true
  ttl_s: 0
healer:
  enabled: true
`

func TestLoadSampleYAML(t *testing.T) {
	path := filepath.Join(t.TempDir(), "wiwi.yaml")
	if err := os.WriteFile(path, []byte(sampleYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	cfg, err := Load(path)
	if err != nil {
		t.Fatalf("Load: %v", err)
	}

	if len(cfg.Providers) != 2 {
		t.Fatalf("providers = %d, want 2", len(cfg.Providers))
	}
	p := cfg.Providers[0]
	if p.Name != "openai-main" || p.ProviderType != "openai" {
		t.Errorf("provider[0] = %q/%q", p.Name, p.ProviderType)
	}
	if p.BaseURL != "https://api.openai.com/v1" {
		t.Errorf("base_url = %q", p.BaseURL)
	}
	if p.TimeoutS != 90 {
		t.Errorf("timeout_s = %v, want 90", p.TimeoutS)
	}
	if p.ExtraHeaders["X-Title"] != "wiwi" {
		t.Errorf("extra_headers = %v", p.ExtraHeaders)
	}
	if p.RoundRobin {
		t.Error("round_robin = true, want explicit false preserved")
	}
	if len(p.Keys) != 2 {
		t.Fatalf("keys = %d, want 2", len(p.Keys))
	}
	if k := p.Keys[0]; k.Label != "main" || k.Key != "sk-main" || k.Weight != 3 || !k.Enabled {
		t.Errorf("keys[0] = %+v", k)
	}
	if k := p.Keys[1]; k.Label != "backup" || k.Weight != 1 || !k.Enabled {
		t.Errorf("keys[1] defaults = %+v", k)
	}
	if cfg.Providers[1].AliasID != "anth" {
		t.Errorf("alias_id = %q", cfg.Providers[1].AliasID)
	}

	if len(cfg.ModelList) != 2 {
		t.Fatalf("model_list = %d, want 2", len(cfg.ModelList))
	}
	m := cfg.ModelList[0]
	if m.ModelName != "my-gpt" || m.Params.Provider != "openai-main" || m.Params.Model != "gpt-4o" {
		t.Errorf("model[0] = %+v", m)
	}
	if m.Params.Weight != 2 {
		t.Errorf("model[0].weight = %d, want 2", m.Params.Weight)
	}
	if m.Params.MaxTokens == nil || *m.Params.MaxTokens != 4096 {
		t.Errorf("model[0].max_tokens = %v", m.Params.MaxTokens)
	}
	if m.Params.RPM == nil || *m.Params.RPM != 500 {
		t.Errorf("model[0].rpm = %v", m.Params.RPM)
	}
	// Lenient flat shape.
	flat := cfg.ModelList[1]
	if flat.ModelName != "flat" || flat.Params.Provider != "anthropic-main" || flat.Params.Model != "claude-3-5-sonnet" {
		t.Errorf("flat model entry = %+v", flat)
	}
	if flat.Params.Weight != 1 {
		t.Errorf("flat weight default = %d, want 1", flat.Params.Weight)
	}

	r := cfg.Router
	if r.Strategy != "least-busy" || r.NumRetries != 4 || r.Timeout != 60 {
		t.Errorf("router = %+v", r)
	}
	if got := r.Fallbacks["my-gpt"]; len(got) != 1 || got[0] != "flat" {
		t.Errorf("fallbacks = %v", r.Fallbacks)
	}
	if a := r.ModelGroupAlias["gpt-4"]; a.Target != "my-gpt" || !a.ForceMapping {
		t.Errorf("alias gpt-4 = %+v", a)
	}
	if a := r.ModelGroupAlias["fancy"]; a.Target != "my-gpt" || a.ForceMapping || a.DisplayName != "Fancy" {
		t.Errorf("alias fancy = %+v", a)
	}
	// Explicit false survives ApplyDefaults.
	if r.StreamLoopDetection {
		t.Error("stream_loop_detection = true, want explicit false preserved")
	}
	// Omitted fields get the Python defaults.
	if !r.StreamEventIDs || !r.StreamJournalEnabled {
		t.Errorf("stream defaults = %+v", r)
	}
	if r.StreamIdleTimeoutS != 30 || r.StreamFirstChunkTimeoutS != 300 || r.StreamPingIntervalS != 15 {
		t.Errorf("stream timeouts = %v/%v/%v", r.StreamIdleTimeoutS, r.StreamFirstChunkTimeoutS, r.StreamPingIntervalS)
	}
	if r.StreamLoopLimit != 100 || r.StreamJournalTTLS != 600 || r.StreamJournalMaxBytes != 1048576 {
		t.Errorf("stream journal defaults = %+v", r)
	}
	if r.StreamJournalDir != ".wiwi/journals" || r.StreamResume != "off" {
		t.Errorf("stream journal dir/resume = %q/%q", r.StreamJournalDir, r.StreamResume)
	}
	if r.FailoverMode != "any_error" || r.KeyMaxConsecutiveFails != 5 || r.CycleEveryN != 3 {
		t.Errorf("failover defaults = %+v", r)
	}
	if r.AllowedFails != 3 || r.CooldownTime != 30 || r.PrometheusPath != "/metrics" {
		t.Errorf("router defaults = %+v", r)
	}

	g := cfg.General
	if g.MasterKey != "sk-test" || g.DatabaseURL != "postgres://user:pw@db/wiwi" {
		t.Errorf("general = %+v", g)
	}
	if len(g.TrustedProxies) != 2 || g.TrustedProxies[0] != "127.0.0.1/32" {
		t.Errorf("trusted_proxies = %v", g.TrustedProxies)
	}
	if g.MaxKeysPerUser != 50 || g.UnpricedModelPolicy != "warn" {
		t.Errorf("general defaults = %+v", g)
	}

	w := cfg.Wiwi
	if w.Host != "127.0.0.1" || w.Port != 8080 {
		t.Errorf("wiwi host/port = %q/%d", w.Host, w.Port)
	}
	if !w.DropParams || !w.StoreResponses || w.ResponseStoreTTLS != 86400 || w.MaxRequestBodyMB != 50 {
		t.Errorf("wiwi defaults = %+v", w)
	}

	if !cfg.Cache.Enabled {
		t.Error("cache enabled = false, want true")
	}
	// ttl_s: 0 is meaningful (disables the cache), so it must not be defaulted.
	if cfg.Cache.TTLS != 0 {
		t.Errorf("cache ttl = %v, want explicit 0 preserved", cfg.Cache.TTLS)
	}
	if cfg.Cache.MaxEntries != 1024 || cfg.Cache.BypassHeader != "x-wiwi-no-cache" {
		t.Errorf("cache defaults = %+v", cfg.Cache)
	}

	if !cfg.Healer.Enabled || cfg.Healer.TickS != 30 || cfg.Healer.ProbeTimeoutS != 10 || cfg.Healer.MaxProbesPerSweep != 8 {
		t.Errorf("healer = %+v", cfg.Healer)
	}
}

func TestEnvInterpolation(t *testing.T) {
	t.Setenv("WIWI_TEST_OPENAI_KEY", "sk-live")
	t.Setenv("WIWI_TEST_PROXIES", "127.0.0.1/32 10.0.0.0/8")
	// Make sure the "missing" variable really is missing.
	os.Unsetenv("WIWI_TEST_ABSENT_KEY")

	raw := `
providers:
  - name: live
    provider: openai
    keys: [{key: os.environ/WIWI_TEST_OPENAI_KEY}]
  - name: gone
    provider: anthropic
    keys: [{key: os.environ/WIWI_TEST_ABSENT_KEY}]
model_list:
  - model_name: kept
    wiwi_params: {provider: live, model: gpt-4o}
  - model_name: dropped
    wiwi_params: {provider: gone, model: claude-3}
general_settings:
  master_key: sk-test
  redis_url: os.environ/WIWI_TEST_ABSENT_KEY
  trusted_proxies: [os.environ/WIWI_TEST_PROXIES]
`
	cfg, err := LoadFromString(raw)
	if err != nil {
		t.Fatalf("LoadFromString: %v", err)
	}
	if len(cfg.Providers) != 1 || cfg.Providers[0].Name != "live" {
		t.Fatalf("providers = %+v, want only live", cfg.Providers)
	}
	if k := cfg.Providers[0].Keys[0]; k.Key != "sk-live" {
		t.Errorf("key = %q, want sk-live", k.Key)
	}
	// KeyDef defaults.
	if k := cfg.Providers[0].Keys[0]; k.Label != "default" || k.Weight != 1 || !k.Enabled {
		t.Errorf("key defaults = %+v", k)
	}
	// Model entries pointing at the dropped provider are removed.
	if len(cfg.ModelList) != 1 || cfg.ModelList[0].ModelName != "kept" {
		t.Fatalf("model_list = %+v, want only kept", cfg.ModelList)
	}
	// A missing env var resolves to "", never an error.
	if cfg.General.RedisURL != "" {
		t.Errorf("redis_url = %q, want empty", cfg.General.RedisURL)
	}
	// The list-valued form splits on commas/whitespace.
	if len(cfg.General.TrustedProxies) != 2 ||
		cfg.General.TrustedProxies[0] != "127.0.0.1/32" ||
		cfg.General.TrustedProxies[1] != "10.0.0.0/8" {
		t.Errorf("trusted_proxies = %v", cfg.General.TrustedProxies)
	}
}

func TestEnvInterpolationScalarList(t *testing.T) {
	// The example config spells trusted_proxies as a bare env reference.
	t.Setenv("WIWI_TEST_PROXIES_2", "10.0.0.0/8,192.168.0.0/16")
	cfg, err := LoadFromString(`
general_settings:
  master_key: sk-test
  trusted_proxies: os.environ/WIWI_TEST_PROXIES_2
`)
	if err != nil {
		t.Fatalf("LoadFromString: %v", err)
	}
	if len(cfg.General.TrustedProxies) != 2 {
		t.Fatalf("trusted_proxies = %v", cfg.General.TrustedProxies)
	}
}

func TestEnvInterpolationNumeric(t *testing.T) {
	// An env-backed scalar must still decode into an int field.
	t.Setenv("WIWI_TEST_PORT", "9001")
	cfg, err := LoadFromString(`
general_settings:
  master_key: sk-test
wiwi_settings:
  port: os.environ/WIWI_TEST_PORT
`)
	if err != nil {
		t.Fatalf("LoadFromString: %v", err)
	}
	if cfg.Wiwi.Port != 9001 {
		t.Errorf("port = %d, want 9001", cfg.Wiwi.Port)
	}
}

func TestApplyDefaults(t *testing.T) {
	cfg := &Config{}
	cfg.ApplyDefaults()

	if cfg.General.DatabaseURL != "sqlite+aiosqlite:///wiwi.db" {
		t.Errorf("database_url = %q", cfg.General.DatabaseURL)
	}
	if cfg.General.MaxKeysPerUser != 50 || cfg.General.UnpricedModelPolicy != "warn" {
		t.Errorf("general = %+v", cfg.General)
	}
	r := cfg.Router
	if r.Strategy != "simple-shuffle" || r.Timeout != 120 || r.NumRetries != 2 ||
		r.AllowedFails != 3 || r.CooldownTime != 30 || r.FailoverMode != "any_error" ||
		r.KeyMaxConsecutiveFails != 5 || r.CycleEveryN != 3 {
		t.Errorf("router defaults = %+v", r)
	}
	if r.StreamIdleTimeoutS != 30 || r.StreamFirstChunkTimeoutS != 300 || r.StreamPingIntervalS != 15 {
		t.Errorf("stream timeouts = %+v", r)
	}
	if !r.StreamLoopDetection || !r.StreamEventIDs || !r.StreamJournalEnabled {
		t.Errorf("stream bools = %+v", r)
	}
	if r.StreamLoopLimit != 100 || r.StreamJournalDir != ".wiwi/journals" ||
		r.StreamJournalTTLS != 600 || r.StreamJournalMaxBytes != 1048576 ||
		r.StreamResume != "off" || r.StreamResumeMaxRetries != 1 {
		t.Errorf("stream journal = %+v", r)
	}
	if r.PrometheusPath != "/metrics" {
		t.Errorf("prometheus_path = %q", r.PrometheusPath)
	}
	if cfg.Wiwi.Host != "0.0.0.0" || cfg.Wiwi.Port != 4000 || cfg.Wiwi.MaxRequestBodyMB != 50 {
		t.Errorf("wiwi = %+v", cfg.Wiwi)
	}
	if !cfg.Wiwi.DropParams || !cfg.Wiwi.StoreResponses || cfg.Wiwi.ResponseStoreTTLS != 86400 {
		t.Errorf("wiwi defaults = %+v", cfg.Wiwi)
	}
	if cfg.Cache.TTLS != 3600 || cfg.Cache.MaxEntries != 1024 || cfg.Cache.BypassHeader != "x-wiwi-no-cache" {
		t.Errorf("cache defaults = %+v", cfg.Cache)
	}
	if cfg.Healer.TickS != 30 || cfg.Healer.ProbeTimeoutS != 10 || cfg.Healer.MaxProbesPerSweep != 8 {
		t.Errorf("healer defaults = %+v", cfg.Healer)
	}

	// Providers and deployments built programmatically get their defaults too.
	cfg.Providers = []Provider{{
		Name:         "p",
		ProviderType: "openai",
		Keys:         []KeyDef{{Key: "sk-1"}},
	}}
	cfg.ModelList = []ModelEntry{{ModelName: "m", Params: DeploymentParams{Provider: "p", Model: "gpt-4o"}}}
	cfg.ApplyDefaults()

	p := cfg.Providers[0]
	if !p.RoundRobin {
		t.Error("round_robin default = false, want true")
	}
	if p.TimeoutS != 120 {
		t.Errorf("provider timeout_s = %v, want router timeout 120", p.TimeoutS)
	}
	if k := p.Keys[0]; k.Label != "default" || k.Weight != 1 || !k.Enabled {
		t.Errorf("key defaults = %+v", k)
	}
	if w := cfg.ModelList[0].Params.Weight; w != 1 {
		t.Errorf("deployment weight = %d, want 1", w)
	}

	// Idempotent.
	before := *cfg
	cfg.ApplyDefaults()
	if cfg.Router.Timeout != before.Router.Timeout || cfg.Cache.TTLS != before.Cache.TTLS ||
		cfg.Providers[0].TimeoutS != before.Providers[0].TimeoutS {
		t.Error("ApplyDefaults is not idempotent")
	}
}

func TestValidate(t *testing.T) {
	base := func() *Config {
		c := &Config{
			Providers: []Provider{{
				Name:         "p",
				ProviderType: "openai",
				Keys:         []KeyDef{{Label: "main", Key: "sk", Weight: 1, Enabled: true}},
			}},
			ModelList: []ModelEntry{{
				ModelName: "m",
				Params:    DeploymentParams{Provider: "p", Model: "gpt-4o", Weight: 1},
			}},
		}
		c.General.MasterKey = "sk-master"
		return c
	}

	if err := base().Validate(); err != nil {
		t.Fatalf("valid config rejected: %v", err)
	}

	cases := []struct {
		name   string
		mutate func(*Config)
		want   string
	}{
		{"master key", func(c *Config) { c.General.MasterKey = "" }, "master_key is required"},
		{"blank master key", func(c *Config) { c.General.MasterKey = "   " }, "master_key is required"},
		{"missing provider type", func(c *Config) { c.Providers[0].ProviderType = "" }, "provider type is required"},
		{"duplicate provider", func(c *Config) {
			c.Providers = append(c.Providers, c.Providers[0])
		}, "duplicate provider name"},
		{"unknown provider", func(c *Config) { c.ModelList[0].Params.Provider = "nope" }, "unknown provider"},
		{"deployment weight", func(c *Config) { c.ModelList[0].Params.Weight = 0 }, "weight must be >= 1"},
		{"key weight", func(c *Config) { c.Providers[0].Keys[0].Weight = 0 }, "weight must be >= 1"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			c := base()
			tc.mutate(c)
			err := c.Validate()
			if err == nil {
				t.Fatalf("Validate() = nil, want error containing %q", tc.want)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("Validate() = %q, want it to contain %q", err, tc.want)
			}
		})
	}
}

func TestLoadFailsClosed(t *testing.T) {
	path := filepath.Join(t.TempDir(), "wiwi.yaml")
	if err := os.WriteFile(path, []byte("general_settings:\n  master_key: \"\"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil || !strings.Contains(err.Error(), "master_key is required") {
		t.Fatalf("Load = %v, want master_key error", err)
	}

	if _, err := Load(filepath.Join(t.TempDir(), "absent.yaml")); err == nil ||
		!strings.Contains(err.Error(), "not found") {
		t.Fatalf("Load(absent) = %v, want not-found error", err)
	}

	if _, err := LoadFromString("providers: [\n"); err == nil ||
		!strings.Contains(err.Error(), "invalid YAML") {
		t.Fatalf("LoadFromString(bad yaml) = %v", err)
	}

	if _, err := LoadFromString("- just\n- a list\n"); err == nil ||
		!strings.Contains(err.Error(), "mapping") {
		t.Fatalf("LoadFromString(list) = %v", err)
	}
}

func TestResolvePrecedence(t *testing.T) {
	dir := t.TempDir()
	t.Chdir(dir)
	// Keep the process env out of the way.
	t.Setenv("WIWI_CONFIG", "")
	os.Unsetenv("WIWI_CONFIG")

	write := func(name, body string) string {
		t.Helper()
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
		return p
	}
	write("wiwi.yaml", "general_settings:\n  master_key: from-file\n")

	// 1. ./wiwi.yaml fallback.
	cfg, err := Resolve("")
	if err != nil {
		t.Fatalf("Resolve(\"\") = %v", err)
	}
	if cfg.General.MasterKey != "from-file" {
		t.Fatalf("master_key = %q, want from-file", cfg.General.MasterKey)
	}

	// 2. WIWI_CONFIG (inline YAML) beats wiwi.yaml.
	t.Setenv("WIWI_CONFIG", "general_settings:\n  master_key: from-env\n")
	cfg, err = Resolve("")
	if err != nil {
		t.Fatalf("Resolve with WIWI_CONFIG = %v", err)
	}
	if cfg.General.MasterKey != "from-env" {
		t.Fatalf("master_key = %q, want from-env", cfg.General.MasterKey)
	}

	// 3. An explicit path beats both.
	explicit := write("other.yaml", "general_settings:\n  master_key: from-path\n")
	cfg, err = Resolve(explicit)
	if err != nil {
		t.Fatalf("Resolve(explicit) = %v", err)
	}
	if cfg.General.MasterKey != "from-path" {
		t.Fatalf("master_key = %q, want from-path", cfg.General.MasterKey)
	}
}

func TestLoadEnv(t *testing.T) {
	dir := t.TempDir()
	t.Chdir(dir)
	t.Setenv("WIWI_TEST_ENV_EXISTING", "keep")
	os.Unsetenv("WIWI_TEST_ENV_FRESH")
	os.Unsetenv("WIWI_TEST_ENV_QUOTED")

	body := strings.Join([]string{
		"# a comment",
		"",
		"WIWI_TEST_ENV_EXISTING=override",
		"export WIWI_TEST_ENV_FRESH=fresh # trailing comment",
		"WIWI_TEST_ENV_QUOTED=\"quoted value\"",
		"malformed-line-without-equals",
	}, "\n")
	if err := os.WriteFile(".env", []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
	LoadEnv()

	if got := os.Getenv("WIWI_TEST_ENV_EXISTING"); got != "keep" {
		t.Errorf("existing var = %q, want keep (never overridden)", got)
	}
	if got := os.Getenv("WIWI_TEST_ENV_FRESH"); got != "fresh" {
		t.Errorf("fresh var = %q, want fresh", got)
	}
	if got := os.Getenv("WIWI_TEST_ENV_QUOTED"); got != "quoted value" {
		t.Errorf("quoted var = %q, want \"quoted value\"", got)
	}
}

func TestProviderTypes(t *testing.T) {
	want := []string{
		"openai", "anthropic", "gemini", "openai-compatible", "openrouter",
		"gmicloud", "bai", "nvidia-nim", "cline", "workbuddy", "opencode",
		"hermes", "ifm",
	}
	got := ProviderTypes()
	if len(got) != len(want) {
		t.Fatalf("ProviderTypes() = %v, want %d entries", got, len(want))
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("ProviderTypes()[%d] = %q, want %q", i, got[i], want[i])
		}
	}
	// Callers must not be able to mutate the source of truth.
	got[0] = "mutated"
	if ProviderTypes()[0] != "openai" {
		t.Error("ProviderTypes() exposes the backing slice")
	}
}
