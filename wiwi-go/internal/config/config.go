// Package config parses the wiwi.yaml configuration — a LiteLLM-like shape —
// into typed Go structs, and mirrors the loading rules of wiwi/config.py.
//
// Two behaviours are load-bearing and must not be "simplified":
//
//   - Env interpolation. Any string of the form "os.environ/NAME" is resolved
//     from the environment before decoding; a missing variable resolves to ""
//     rather than failing, so the shipped example (which references eleven
//     optional provider keys) loads in a fresh container. A single-element list
//     of the form [os.environ/NAME] is split on commas and whitespace into a
//     []string, which is how a container turns on trusted_proxies without
//     shipping its own YAML.
//
//   - Empty-key provider filtering. A provider whose every key resolved to ""
//     is dropped, together with every model_list entry that references it, and
//     the drop is logged with the env var names involved. Silent, a one-letter
//     typo in an os.environ/NAME reference deletes a provider and its models
//     and later reads as "model not found" — a routing problem, not a config
//     one.
//
// Config precedence (Resolve): an explicit --config path beats WIWI_CONFIG
// (inline YAML, for containers) beats ./wiwi.yaml.
package config

import (
	"errors"
	"fmt"
	"log"
	"os"
	"strings"

	"gopkg.in/yaml.v3"
)

// envPrefix marks a string as an environment reference: "os.environ/NAME".
const envPrefix = "os.environ/"

// providerTypes is the single source of truth for the provider types this
// gateway can route to. Mirrors wiwi/config.py PROVIDER_TYPES and
// providers.get_adapter().
var providerTypes = []string{
	"openai",
	"anthropic",
	"gemini",
	"openai-compatible",
	"openrouter",
	"gmicloud",
	"bai",
	"nvidia-nim",
	"cline",
	"workbuddy",
	"opencode",
	"hermes",
	"ifm",
}

// ProviderTypes returns the known provider types, in DESIGN order.
func ProviderTypes() []string {
	out := make([]string, len(providerTypes))
	copy(out, providerTypes)
	return out
}

// setFlags records which YAML keys were present in a mapping. It is what lets
// ApplyDefaults tell an explicit `false` / `0` from an omitted field: the Go
// zero value cannot. It is unexported and never serialized, so it cannot leak
// into a round-trip.
type setFlags map[string]bool

func (s setFlags) has(key string) bool { return s[key] }

// nodeKeys lists the keys of a YAML mapping node (nil for a non-mapping).
func nodeKeys(n *yaml.Node) setFlags {
	if n == nil || n.Kind != yaml.MappingNode {
		return nil
	}
	out := make(setFlags, len(n.Content)/2)
	for i := 0; i+1 < len(n.Content); i += 2 {
		out[n.Content[i].Value] = true
	}
	return out
}

// mapValue returns the value node for key in a mapping node, or nil.
func mapValue(n *yaml.Node, key string) *yaml.Node {
	if n == nil || n.Kind != yaml.MappingNode {
		return nil
	}
	for i := 0; i+1 < len(n.Content); i += 2 {
		if n.Content[i].Value == key {
			return n.Content[i+1]
		}
	}
	return nil
}

// scalarValue returns a scalar node's literal text, or "".
func scalarValue(n *yaml.Node) string {
	if n == nil || n.Kind != yaml.ScalarNode {
		return ""
	}
	return n.Value
}

// splitList splits a list-valued env var on commas and/or whitespace.
func splitList(v string) []string {
	return strings.FieldsFunc(v, func(r rune) bool {
		return r == ',' || r == ' ' || r == '\t' || r == '\n' || r == '\r'
	})
}

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

// KeyDef is one credential in a provider's key pool.
type KeyDef struct {
	Label   string `yaml:"label"`
	Key     string `yaml:"key"`
	Weight  int    `yaml:"weight"`
	Enabled bool   `yaml:"enabled"`

	set setFlags
}

func (k *KeyDef) UnmarshalYAML(n *yaml.Node) error {
	type plain KeyDef
	var v plain
	if err := n.Decode(&v); err != nil {
		return err
	}
	*k = KeyDef(v)
	k.set = nodeKeys(n)
	return nil
}

// Provider is one upstream account. ProviderType is spelled `provider:` in
// YAML (not `type:`) to match the real config shape.
type Provider struct {
	Name         string            `yaml:"name"`
	ProviderType string            `yaml:"provider"`
	BaseURL      string            `yaml:"base_url"`
	TimeoutS     float64           `yaml:"timeout_s"`
	ExtraHeaders map[string]string `yaml:"extra_headers"`
	RoundRobin   bool              `yaml:"round_robin"`
	Keys         []KeyDef          `yaml:"keys"`
	AliasID      string            `yaml:"alias_id"`

	set setFlags
}

func (p *Provider) UnmarshalYAML(n *yaml.Node) error {
	type plain Provider
	var v plain
	if err := n.Decode(&v); err != nil {
		return err
	}
	*p = Provider(v)
	p.set = nodeKeys(n)
	return nil
}

// DeploymentParams is the wiwi_params block of a model_list entry: which
// provider account serves the group, the provider-native model id, and the
// per-deployment knobs.
type DeploymentParams struct {
	Provider              string            `yaml:"provider"`
	Model                 string            `yaml:"model"`
	Weight                int               `yaml:"weight"`
	MaxTokens             *int              `yaml:"max_tokens"`
	RPM                   *int              `yaml:"rpm"`
	TPM                   *int              `yaml:"tpm"`
	MaxInflight           *int              `yaml:"max_inflight"`
	Timeout               *float64          `yaml:"timeout"`
	ExtraHeaders          map[string]string `yaml:"extra_headers"`
	ExtraBody             map[string]any    `yaml:"extra_body"`
	PromptCache           bool              `yaml:"prompt_cache"`
	PromptCacheMinTokens  *int              `yaml:"prompt_cache_min_tokens"`
	OpencodeFreeTierCloak *bool             `yaml:"opencode_free_tier_cloak"`

	set setFlags
}

func (d *DeploymentParams) UnmarshalYAML(n *yaml.Node) error {
	type plain DeploymentParams
	var v plain
	if err := n.Decode(&v); err != nil {
		return err
	}
	*d = DeploymentParams(v)
	d.set = nodeKeys(n)
	return nil
}

// ModelEntry maps a caller-facing model_name to one deployment.
type ModelEntry struct {
	ModelName string           `yaml:"model_name"`
	Params    DeploymentParams `yaml:"wiwi_params"`
}

// UnmarshalYAML accepts the canonical `wiwi_params:` shape and, leniently, the
// older flat shape that put provider/model (and weight) at the entry level.
func (m *ModelEntry) UnmarshalYAML(n *yaml.Node) error {
	type plain ModelEntry
	var v plain
	if err := n.Decode(&v); err != nil {
		return err
	}
	*m = ModelEntry(v)
	if m.Params.Provider != "" && m.Params.Model != "" {
		return nil
	}
	var flat struct {
		Provider string `yaml:"provider"`
		Model    string `yaml:"model"`
		Weight   *int   `yaml:"weight"`
	}
	if err := n.Decode(&flat); err != nil {
		return nil // not the flat shape either; leave it for Validate to report
	}
	if m.Params.Provider == "" {
		m.Params.Provider = flat.Provider
	}
	if m.Params.Model == "" {
		m.Params.Model = flat.Model
	}
	if flat.Weight != nil && !m.Params.set.has("weight") {
		m.Params.Weight = *flat.Weight
		m.Params.set = m.Params.set.with("weight")
	}
	return nil
}

func (s setFlags) with(key string) setFlags {
	out := make(setFlags, len(s)+1)
	for k, v := range s {
		out[k] = v
	}
	out[key] = true
	return out
}

// AliasEntry is a model_group_alias target. YAML may spell it as a bare string
// (`gpt-4: gpt-4o`) or as a rich mapping.
type AliasEntry struct {
	Target       string `yaml:"target"`
	ForceMapping bool   `yaml:"force_mapping"`
	DisplayName  string `yaml:"display_name"`

	set setFlags
}

func (a *AliasEntry) UnmarshalYAML(n *yaml.Node) error {
	if n.Kind == yaml.ScalarNode {
		var target string
		if err := n.Decode(&target); err != nil {
			return err
		}
		*a = AliasEntry{Target: target, ForceMapping: true}
		return nil
	}
	type plain AliasEntry
	var v plain
	if err := n.Decode(&v); err != nil {
		return err
	}
	*a = AliasEntry(v)
	a.set = nodeKeys(n)
	if !a.set.has("force_mapping") {
		a.ForceMapping = true
	}
	return nil
}

// RouterSettings is the `router_settings:` block.
type RouterSettings struct {
	Strategy               string                `yaml:"routing_strategy"`
	NumRetries             int                   `yaml:"num_retries"`
	AllowedFails           int                   `yaml:"allowed_fails"`
	KeyMaxConsecutiveFails int                   `yaml:"key_max_consecutive_fails"`
	CycleEveryN            int                   `yaml:"cycle_every_n"`
	Timeout                float64               `yaml:"timeout"`
	CooldownTime           float64               `yaml:"cooldown_time"`
	FailoverMode           string                `yaml:"failover_mode"`
	Fallbacks              map[string][]string   `yaml:"fallbacks"`
	ContextWindowFallbacks map[string][]string   `yaml:"context_window_fallbacks"`
	ModelGroupAlias        map[string]AliasEntry `yaml:"model_group_alias"`
	GlobalRPM              *int                  `yaml:"global_rpm"`
	GlobalTPM              *int                  `yaml:"global_tpm"`

	SessionAffinity bool `yaml:"session_affinity"`
	PrefixAffinity  bool `yaml:"prefix_affinity"`

	HealthModel         string             `yaml:"health_model"`
	HealthEWMAAlpha     float64            `yaml:"health_ewma_alpha"`
	HealthWindow        int                `yaml:"health_window"`
	AdaptiveCooldown    bool               `yaml:"adaptive_cooldown"`
	MaxInflight         *int               `yaml:"max_inflight"`
	PriorityLanes       map[string]float64 `yaml:"priority_lanes"`
	DefaultLane         string             `yaml:"default_lane"`
	SessionAffinityTTLS float64            `yaml:"session_affinity_ttl_s"`
	PrefixAffinityTTLS  float64            `yaml:"prefix_affinity_ttl_s"`
	InflightRetryAfterS float64            `yaml:"inflight_retry_after_s"`

	StreamIdleTimeoutS       float64 `yaml:"stream_idle_timeout_s"`
	StreamFirstChunkTimeoutS float64 `yaml:"stream_first_chunk_timeout_s"`
	StreamPingIntervalS      float64 `yaml:"stream_ping_interval_s"`
	StreamLoopDetection      bool    `yaml:"stream_loop_detection"`
	StreamCoalesce           bool    `yaml:"stream_coalesce"`
	StreamCoalesceMaxBytes   int     `yaml:"stream_coalesce_max_bytes"`
	StreamCoalesceMaxMS      float64 `yaml:"stream_coalesce_max_ms"`
	StreamGraceDrainS        float64 `yaml:"stream_grace_drain_s"`
	StreamEventIDs           bool    `yaml:"stream_event_ids"`
	StreamJournalEnabled     bool    `yaml:"stream_journal_enabled"`
	StreamLoopLimit          int     `yaml:"stream_loop_limit"`
	StreamResumeMaxRetries   int     `yaml:"stream_resume_max_retries"`
	StreamJournalTTLS        float64 `yaml:"stream_journal_ttl_s"`
	StreamJournalMaxBytes    int     `yaml:"stream_journal_max_bytes"`
	StreamResume             string  `yaml:"stream_resume"`
	StreamJournalDir         string  `yaml:"stream_journal_dir"`

	PrometheusEnabled bool   `yaml:"prometheus_enabled"`
	PrometheusPath    string `yaml:"prometheus_path"`

	set setFlags
}

func (r *RouterSettings) UnmarshalYAML(n *yaml.Node) error {
	type plain RouterSettings
	var v plain
	if err := n.Decode(&v); err != nil {
		return err
	}
	*r = RouterSettings(v)
	r.set = nodeKeys(n)
	return nil
}

// GeneralSettings is the `general_settings:` block.
type GeneralSettings struct {
	MasterKey           string   `yaml:"master_key"`
	DatabaseURL         string   `yaml:"database_url"`
	RedisURL            string   `yaml:"redis_url"`
	MaxKeysPerUser      int      `yaml:"max_keys_per_user"`
	UnpricedModelPolicy string   `yaml:"unpriced_model_policy"`
	TrustedProxies      []string `yaml:"trusted_proxies"`
	SharedDatabase      bool     `yaml:"shared_database"`
	ReservationStaleS   float64  `yaml:"reservation_stale_s"`
	DBPoolSize          int      `yaml:"db_pool_size"`
	DBMaxOverflow       int      `yaml:"db_max_overflow"`
	DBPoolRecycleS      float64  `yaml:"db_pool_recycle_s"`

	set setFlags
}

// UnmarshalYAML also accepts trusted_proxies as a comma/whitespace-separated
// string, which is how the example config declares it:
// `trusted_proxies: os.environ/WIWI_TRUSTED_PROXIES`.
func (g *GeneralSettings) UnmarshalYAML(n *yaml.Node) error {
	var raw struct {
		MasterKey           string    `yaml:"master_key"`
		DatabaseURL         string    `yaml:"database_url"`
		RedisURL            string    `yaml:"redis_url"`
		MaxKeysPerUser      int       `yaml:"max_keys_per_user"`
		UnpricedModelPolicy string    `yaml:"unpriced_model_policy"`
		TrustedProxies      yaml.Node `yaml:"trusted_proxies"`
		SharedDatabase      bool      `yaml:"shared_database"`
	}
	if err := n.Decode(&raw); err != nil {
		return err
	}
	g.MasterKey = raw.MasterKey
	g.DatabaseURL = raw.DatabaseURL
	g.RedisURL = raw.RedisURL
	g.MaxKeysPerUser = raw.MaxKeysPerUser
	g.UnpricedModelPolicy = raw.UnpricedModelPolicy
	g.SharedDatabase = raw.SharedDatabase
	g.TrustedProxies = nil
	switch raw.TrustedProxies.Kind {
	case 0: // absent
	case yaml.ScalarNode:
		g.TrustedProxies = splitList(raw.TrustedProxies.Value)
	case yaml.SequenceNode:
		if err := raw.TrustedProxies.Decode(&g.TrustedProxies); err != nil {
			return err
		}
	default:
		return fmt.Errorf("general_settings.trusted_proxies must be a list or a string")
	}
	g.set = nodeKeys(n)
	return nil
}

// WiwiSettings is the `wiwi_settings:` block (process-level knobs).
type WiwiSettings struct {
	DropParams       bool `yaml:"drop_params"`
	MaxRequestBodyMB int  `yaml:"max_request_body_mb"`

	StoreResponses    bool    `yaml:"store_responses"`
	ResponseStoreTTLS float64 `yaml:"response_store_ttl_s"`

	Host      string `yaml:"host"`
	Port      int    `yaml:"port"`
	PublicURL string `yaml:"public_url"`

	SpendBatchingEnabled bool `yaml:"spend_batching_enabled"`
	SingleflightEnabled  bool `yaml:"singleflight_enabled"`
	PerfTimings          bool `yaml:"perf_timings"`

	StorePromptsInSpendLogs    bool    `yaml:"store_prompts_in_spend_logs"`
	LogRetentionDays           int     `yaml:"log_retention_days"`
	LogMaxRows                 int     `yaml:"log_max_rows"`
	LogPruneIntervalS          float64 `yaml:"log_prune_interval_s"`
	SpendFlushIntervalMS       int     `yaml:"spend_flush_interval_ms"`
	SpendFlushMaxPending       int     `yaml:"spend_flush_max_pending"`
	SpendFlushMaxDollarsPerKey float64 `yaml:"spend_flush_max_dollars_per_key"`
	ToolSchemaCacheEntries     int     `yaml:"tool_schema_cache_entries"`
	HTTPMaxConnections         int     `yaml:"http_max_connections"`
	HTTPMaxKeepalive           int     `yaml:"http_max_keepalive_connections"`

	set setFlags
}

func (w *WiwiSettings) UnmarshalYAML(n *yaml.Node) error {
	type plain WiwiSettings
	var v plain
	if err := n.Decode(&v); err != nil {
		return err
	}
	*w = WiwiSettings(v)
	w.set = nodeKeys(n)
	return nil
}

// CacheSettings is the `cache_settings:` block (exact-match response cache).
type CacheSettings struct {
	Enabled      bool    `yaml:"enabled"`
	TTLS         float64 `yaml:"ttl_s"`
	MaxEntries   int     `yaml:"max_entries"`
	BypassHeader string  `yaml:"bypass_header"`

	set setFlags
}

func (c *CacheSettings) UnmarshalYAML(n *yaml.Node) error {
	type plain CacheSettings
	var v plain
	if err := n.Decode(&v); err != nil {
		return err
	}
	*c = CacheSettings(v)
	c.set = nodeKeys(n)
	return nil
}

// HealerSettings is the `healer:` block (background key/deployment prober).
type HealerSettings struct {
	Enabled           bool    `yaml:"enabled"`
	TickS             float64 `yaml:"tick_s"`
	ProbeTimeoutS     float64 `yaml:"probe_timeout_s"`
	MaxProbesPerSweep int     `yaml:"max_probes_per_sweep"`
	MinProbeIntervalS float64 `yaml:"min_probe_interval_s"`
	ProbeBackoffBaseS float64 `yaml:"probe_backoff_base_s"`
	ProbeBackoffCapS  float64 `yaml:"probe_backoff_cap_s"`
	ProbesToRestore   int     `yaml:"probes_to_restore"`
	ProbationWeight   float64 `yaml:"probation_weight"`
}

// Config is the whole parsed wiwi.yaml.
type RealtimeSettings struct {
	Enabled         bool    `yaml:"enabled"`
	AllowKeyInQuery bool    `yaml:"allow_key_in_query"`
	IdleTimeoutS    float64 `yaml:"idle_timeout_s"`
	MaxSessionS     float64 `yaml:"max_session_s"`
}

type TelemetrySettings struct {
	Enabled     bool              `yaml:"enabled"`
	Endpoint    string            `yaml:"endpoint"`
	ServiceName string            `yaml:"service_name"`
	SampleRatio float64           `yaml:"sample_ratio"`
	Headers     map[string]string `yaml:"headers"`
}

type Config struct {
	Providers []Provider        `yaml:"providers"`
	ModelList []ModelEntry      `yaml:"model_list"`
	Router    RouterSettings    `yaml:"router_settings"`
	General   GeneralSettings   `yaml:"general_settings"`
	Wiwi      WiwiSettings      `yaml:"wiwi_settings"`
	Cache     CacheSettings     `yaml:"cache_settings"`
	Healer    HealerSettings    `yaml:"healer"`
	Telemetry TelemetrySettings `yaml:"telemetry"`
	Realtime  RealtimeSettings  `yaml:"realtime"`
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

// Load reads and validates the config at path. It does not read .env — call
// LoadEnv first (Resolve does) if the file references os.environ/ values that
// live there.
func Load(path string) (*Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil, fmt.Errorf("config file not found: %s", path)
		}
		return nil, fmt.Errorf("cannot read config file %s: %w", path, err)
	}
	return buildConfig(data, path)
}

// LoadFromString parses inline YAML (the WIWI_CONFIG env var, for containers).
func LoadFromString(raw string) (*Config, error) {
	return buildConfig([]byte(raw), "WIWI_CONFIG")
}

// Resolve applies the documented precedence: configPath > WIWI_CONFIG (inline
// YAML) > ./wiwi.yaml. It loads .env into the process environment first.
func Resolve(configPath string) (*Config, error) {
	LoadEnv()
	if configPath != "" {
		return Load(configPath)
	}
	if inline := os.Getenv("WIWI_CONFIG"); inline != "" {
		return LoadFromString(inline)
	}
	return Load("wiwi.yaml")
}

// LoadEnv loads .env from the working directory into the process environment.
// Existing environment variables always win, so a container's real env is
// never shadowed by a stale file. Missing/unreadable .env is not an error.
func LoadEnv() {
	data, err := os.ReadFile(".env")
	if err != nil {
		return
	}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(strings.TrimSuffix(line, "\r"))
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		if key == "" {
			continue
		}
		if _, exists := os.LookupEnv(key); exists {
			continue
		}
		value = strings.TrimSpace(value)
		if n := len(value); n >= 2 &&
			((value[0] == '"' && value[n-1] == '"') || (value[0] == '\'' && value[n-1] == '\'')) {
			value = value[1 : n-1]
		} else if i := strings.Index(value, " #"); i >= 0 {
			value = strings.TrimSpace(value[:i])
		}
		os.Setenv(key, value)
	}
}

// buildConfig is the shared pipeline: parse the YAML tree, capture the env var
// names the providers reference, interpolate, decode, filter, default, validate.
func buildConfig(raw []byte, source string) (*Config, error) {
	var doc yaml.Node
	if err := yaml.Unmarshal(raw, &doc); err != nil {
		return nil, fmt.Errorf("invalid YAML in %s: %w", source, err)
	}
	cfg := &Config{}
	if len(doc.Content) > 0 {
		top := doc.Content[0]
		if top.Kind != yaml.MappingNode {
			return nil, fmt.Errorf("%s must contain a YAML mapping at top level", source)
		}
		// Captured before interpolation: once resolved, the variable name is gone.
		keyEnvs := keyEnvVars(top)
		interpolateNode(top)
		if err := top.Decode(cfg); err != nil {
			return nil, fmt.Errorf("invalid config in %s: %w", source, err)
		}
		cfg.filterEmptyProviders(keyEnvs)
	}
	// Env overrides the file, mirroring the Python server: DATABASE_URL and
	// REDIS_URL let a container or start.sh point the backend at a different
	// store/cache without rewriting wiwi.yaml.
	if v := os.Getenv("DATABASE_URL"); v != "" {
		cfg.General.DatabaseURL = v
	}
	if v := os.Getenv("REDIS_URL"); v != "" {
		cfg.General.RedisURL = v
	}
	cfg.ApplyDefaults()
	if err := cfg.Validate(); err != nil {
		return nil, fmt.Errorf("invalid config in %s: %w", source, err)
	}
	return cfg, nil
}

// keyEnvVars maps provider name -> env var names referenced by its keys[],
// read from the un-interpolated tree.
func keyEnvVars(top *yaml.Node) map[string][]string {
	out := make(map[string][]string)
	providers := mapValue(top, "providers")
	if providers == nil || providers.Kind != yaml.SequenceNode {
		return out
	}
	for _, p := range providers.Content {
		if p.Kind != yaml.MappingNode {
			continue
		}
		keys := mapValue(p, "keys")
		if keys == nil || keys.Kind != yaml.SequenceNode {
			continue
		}
		var names []string
		for _, k := range keys.Content {
			if k.Kind != yaml.MappingNode {
				continue
			}
			if kv := scalarValue(mapValue(k, "key")); strings.HasPrefix(kv, envPrefix) {
				names = append(names, strings.TrimPrefix(kv, envPrefix))
			}
		}
		if len(names) > 0 {
			out[scalarValue(mapValue(p, "name"))] = names
		}
	}
	return out
}

// interpolateNode resolves os.environ/NAME references in place, recursively.
func interpolateNode(n *yaml.Node) {
	if n == nil {
		return
	}
	switch n.Kind {
	case yaml.DocumentNode:
		for _, c := range n.Content {
			interpolateNode(c)
		}
	case yaml.MappingNode:
		for i := 1; i < len(n.Content); i += 2 {
			interpolateNode(n.Content[i])
		}
	case yaml.SequenceNode:
		// The list-valued form: [os.environ/NAME] -> one element per value.
		if len(n.Content) == 1 {
			c := n.Content[0]
			if c.Kind == yaml.ScalarNode && strings.HasPrefix(c.Value, envPrefix) {
				values := splitList(os.Getenv(strings.TrimPrefix(c.Value, envPrefix)))
				nodes := make([]*yaml.Node, 0, len(values))
				for _, v := range values {
					nodes = append(nodes, &yaml.Node{Kind: yaml.ScalarNode, Value: v})
				}
				n.Content = nodes
				n.Style = 0
				return
			}
		}
		for _, c := range n.Content {
			interpolateNode(c)
		}
	case yaml.ScalarNode:
		if strings.HasPrefix(n.Value, envPrefix) {
			n.Value = os.Getenv(strings.TrimPrefix(n.Value, envPrefix))
			// Empty tag => yaml re-resolves the value, so an env-backed number
			// still decodes into an int/float field.
			n.Tag = ""
			n.Style = 0
		}
	}
}

// filterEmptyProviders drops providers whose keys all resolved to "" (their
// env vars are unset), logs why, and drops the model_list entries that pointed
// at them.
func (c *Config) filterEmptyProviders(keyEnvs map[string][]string) {
	kept := c.Providers[:0]
	removed := make(map[string]bool)
	for _, p := range c.Providers {
		if len(p.Keys) == 0 {
			// keys: [] (or absent) is an authoring error, not a missing env var.
			kept = append(kept, p)
			continue
		}
		real := p.Keys[:0]
		for _, k := range p.Keys {
			if strings.TrimSpace(k.Key) != "" {
				real = append(real, k)
			}
		}
		if len(real) > 0 {
			p.Keys = real
			kept = append(kept, p)
			continue
		}
		removed[p.Name] = true
		envs := "none declared"
		if names := keyEnvs[p.Name]; len(names) > 0 {
			envs = strings.Join(names, ", ")
		}
		log.Printf("wiwi: config: dropping provider %q: every key resolved to an empty value (env vars: %s)",
			p.Name, envs)
	}
	c.Providers = kept
	if len(removed) == 0 {
		return
	}
	keptModels := c.ModelList[:0]
	for _, m := range c.ModelList {
		if removed[m.Params.Provider] {
			continue
		}
		keptModels = append(keptModels, m)
	}
	c.ModelList = keptModels
}

// ---------------------------------------------------------------------------
// Defaults
// ---------------------------------------------------------------------------

// ApplyDefaults fills every unset field with the Python defaults. It is
// idempotent, and it respects an explicit `0`/`false` written in YAML (the
// presence flags recorded at decode time distinguish that from an omitted
// field).
func (c *Config) ApplyDefaults() {
	if c == nil {
		return
	}
	c.Router.applyDefaults()
	c.General.applyDefaults()
	c.Wiwi.applyDefaults()
	c.Cache.applyDefaults()
	c.Healer.applyDefaults()
	c.Realtime.applyDefaults()

	for i := range c.Providers {
		p := &c.Providers[i]
		if !p.set.has("round_robin") {
			p.RoundRobin = true
		}
		if p.TimeoutS <= 0 {
			// Resolved once here so the gateway never passes "no timeout".
			p.TimeoutS = c.Router.Timeout
		}
		for j := range p.Keys {
			k := &p.Keys[j]
			if k.Label == "" {
				k.Label = "default"
			}
			if k.Weight < 1 && !k.set.has("weight") {
				k.Weight = 1
			}
			if !k.set.has("enabled") {
				k.Enabled = true
			}
		}
	}
	for i := range c.ModelList {
		d := &c.ModelList[i].Params
		if d.Weight < 1 && !d.set.has("weight") {
			d.Weight = 1
		}
	}
}

func (r *RouterSettings) applyDefaults() {
	if r.Strategy == "" {
		r.Strategy = "simple-shuffle"
	}
	if r.NumRetries == 0 && !r.set.has("num_retries") {
		r.NumRetries = 2
	}
	if r.AllowedFails == 0 && !r.set.has("allowed_fails") {
		r.AllowedFails = 3
	}
	if r.KeyMaxConsecutiveFails == 0 && !r.set.has("key_max_consecutive_fails") {
		r.KeyMaxConsecutiveFails = 5
	}
	if r.CycleEveryN == 0 && !r.set.has("cycle_every_n") {
		r.CycleEveryN = 3
	}
	if r.Timeout == 0 && !r.set.has("timeout") {
		r.Timeout = 120
	}
	if r.CooldownTime == 0 && !r.set.has("cooldown_time") {
		r.CooldownTime = 30
	}
	if r.FailoverMode == "" {
		r.FailoverMode = "any_error"
	}
	if r.HealthModel == "" {
		r.HealthModel = "none"
	}
	if r.HealthEWMAAlpha == 0 {
		r.HealthEWMAAlpha = 0.3
	}
	if r.HealthWindow == 0 {
		r.HealthWindow = 50
	}
	if r.DefaultLane == "" {
		r.DefaultLane = "bulk"
	}
	if r.SessionAffinityTTLS == 0 {
		r.SessionAffinityTTLS = 600
	}
	if r.PrefixAffinityTTLS == 0 {
		r.PrefixAffinityTTLS = 600
	}
	if r.InflightRetryAfterS == 0 {
		r.InflightRetryAfterS = 1
	}

	if r.StreamIdleTimeoutS == 0 && !r.set.has("stream_idle_timeout_s") {
		r.StreamIdleTimeoutS = 30
	}
	if r.StreamFirstChunkTimeoutS == 0 && !r.set.has("stream_first_chunk_timeout_s") {
		r.StreamFirstChunkTimeoutS = 300
	}
	if r.StreamPingIntervalS == 0 && !r.set.has("stream_ping_interval_s") {
		r.StreamPingIntervalS = 15
	}
	if !r.set.has("stream_loop_detection") {
		r.StreamLoopDetection = true
	}
	if !r.set.has("stream_event_ids") {
		r.StreamEventIDs = true
	}
	if !r.set.has("stream_journal_enabled") {
		r.StreamJournalEnabled = true
	}
	if r.StreamCoalesceMaxBytes == 0 {
		r.StreamCoalesceMaxBytes = 8192
	}
	if r.StreamCoalesceMaxMS == 0 {
		r.StreamCoalesceMaxMS = 50
	}
	if r.StreamLoopLimit == 0 && !r.set.has("stream_loop_limit") {
		r.StreamLoopLimit = 100
	}
	if r.StreamResumeMaxRetries == 0 && !r.set.has("stream_resume_max_retries") {
		r.StreamResumeMaxRetries = 1
	}
	if r.StreamResume == "" {
		r.StreamResume = "off"
	}
	if r.StreamJournalDir == "" {
		r.StreamJournalDir = ".wiwi/journals"
	}
	if r.StreamJournalTTLS == 0 && !r.set.has("stream_journal_ttl_s") {
		r.StreamJournalTTLS = 600
	}
	if r.StreamJournalMaxBytes == 0 && !r.set.has("stream_journal_max_bytes") {
		r.StreamJournalMaxBytes = 1048576
	}
	if r.PrometheusPath == "" {
		r.PrometheusPath = "/metrics"
	}
}

func (g *GeneralSettings) applyDefaults() {
	if g.DatabaseURL == "" {
		g.DatabaseURL = "sqlite+aiosqlite:///wiwi.db"
	}
	if g.MaxKeysPerUser < 1 {
		g.MaxKeysPerUser = 50
	}
	if g.UnpricedModelPolicy == "" {
		g.UnpricedModelPolicy = "warn"
	}
}

func (w *WiwiSettings) applyDefaults() {
	if !w.set.has("drop_params") {
		w.DropParams = true
	}
	if w.MaxRequestBodyMB == 0 && !w.set.has("max_request_body_mb") {
		w.MaxRequestBodyMB = 50
	}
	if !w.set.has("store_responses") {
		w.StoreResponses = true
	}
	if w.ResponseStoreTTLS == 0 && !w.set.has("response_store_ttl_s") {
		w.ResponseStoreTTLS = 86400
	}
	if w.LogRetentionDays == 0 && !w.set.has("log_retention_days") {
		w.LogRetentionDays = 30
	}
	if w.LogMaxRows == 0 && !w.set.has("log_max_rows") {
		w.LogMaxRows = 10000
	}
	if w.LogPruneIntervalS == 0 && !w.set.has("log_prune_interval_s") {
		w.LogPruneIntervalS = 3600
	}
	if w.Host == "" {
		w.Host = "0.0.0.0"
	}
	if w.Port == 0 && !w.set.has("port") {
		w.Port = 4000
	}
}

func (c *CacheSettings) applyDefaults() {
	if c.TTLS == 0 && !c.set.has("ttl_s") {
		c.TTLS = 3600
	}
	if c.MaxEntries == 0 && !c.set.has("max_entries") {
		c.MaxEntries = 1024
	}
	if c.BypassHeader == "" {
		c.BypassHeader = "x-wiwi-no-cache"
	}
}

func (r *RealtimeSettings) applyDefaults() {
	if r.IdleTimeoutS == 0 {
		r.IdleTimeoutS = 300
	}
	if r.MaxSessionS == 0 {
		r.MaxSessionS = 3600
	}
}

func (h *HealerSettings) applyDefaults() {
	if h.TickS <= 0 {
		h.TickS = 30
	}
	if h.ProbeTimeoutS <= 0 {
		h.ProbeTimeoutS = 10
	}
	if h.MaxProbesPerSweep <= 0 {
		h.MaxProbesPerSweep = 8
	}
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

// Validate enforces the fail-closed invariants: a master key, unique provider
// names, positive weights, and model entries that reference a real provider.
func (c *Config) Validate() error {
	if strings.TrimSpace(c.General.MasterKey) == "" {
		return errors.New("master_key is required")
	}
	seen := make(map[string]struct{}, len(c.Providers))
	for i := range c.Providers {
		p := &c.Providers[i]
		if strings.TrimSpace(p.Name) == "" {
			return fmt.Errorf("providers[%d]: name is required", i)
		}
		if strings.TrimSpace(p.ProviderType) == "" {
			return fmt.Errorf("provider %q: provider type is required (one of %s)",
				p.Name, strings.Join(ProviderTypes(), ", "))
		}
		if _, dup := seen[p.Name]; dup {
			return fmt.Errorf("duplicate provider name %q", p.Name)
		}
		seen[p.Name] = struct{}{}
		if len(p.Keys) == 0 {
			return fmt.Errorf("provider %q needs at least one key entry", p.Name)
		}
		for j := range p.Keys {
			if p.Keys[j].Weight < 1 {
				return fmt.Errorf("provider %q key %q: weight must be >= 1", p.Name, p.Keys[j].Label)
			}
		}
	}
	for i := range c.ModelList {
		m := &c.ModelList[i]
		if strings.TrimSpace(m.ModelName) == "" {
			return fmt.Errorf("model_list[%d]: model_name is required", i)
		}
		if m.Params.Provider == "" {
			return fmt.Errorf("model %q: provider is required", m.ModelName)
		}
		if _, ok := seen[m.Params.Provider]; !ok {
			return fmt.Errorf("model %q references unknown provider %q", m.ModelName, m.Params.Provider)
		}
		if m.Params.Weight < 1 {
			return fmt.Errorf("model %q: weight must be >= 1", m.ModelName)
		}
	}
	return nil
}
