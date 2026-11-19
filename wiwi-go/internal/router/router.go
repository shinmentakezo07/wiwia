package router

import (
	"fmt"
	"strings"
	"sync"
	"time"

	"wiwi-go/internal/config"
	"wiwi-go/internal/providers"
)

// ProviderKey is one credential in a provider account's pool.
type ProviderKey struct {
	Label    string
	Secret   string
	Weight   int
	Enabled  bool
	State    string // "active" | "cooling" | "invalid" | "disabled"
	Until    time.Time
	ErrCount int
	InFlight int
	current  float64

	// health scoring
	ewma      float64
	success   float64
	samples   int
	probation bool
}

func (k *ProviderKey) Available(now time.Time) bool {
	if k == nil || !k.Enabled || k.State == "disabled" || k.State == "invalid" {
		return false
	}
	if k.State == "cooling" && now.Before(k.Until) {
		return false
	}
	return true
}

func (k *ProviderKey) MarkCooling(seconds float64) {
	k.State = "cooling"
	k.Until = time.Now().Add(time.Duration(seconds * float64(time.Second)))
}

func (k *ProviderKey) MarkInvalid() {
	k.State = "invalid"
	k.Until = time.Now().Add(10 * time.Minute)
}

func (k *ProviderKey) MarkActive() {
	k.State = "active"
	k.Until = time.Time{}
	k.ErrCount = 0
}

// ProviderAccount groups keys for one configured provider.
type ProviderAccount struct {
	Name         string
	Type         string
	BaseURL      string
	TimeoutS     float64
	ExtraHeaders map[string]string
	RoundRobin   bool
	AliasID      string
	Keys         []*ProviderKey
	mu           sync.Mutex
}

// Deployment is one model in a group routed to one provider account.
type Deployment struct {
	Group                  string
	Model                  string
	Provider               *ProviderAccount
	Weight                 int
	MaxTokens              *int
	RPM                    *int
	TPM                    *int
	MaxInflight            *int
	Timeout                *float64
	ExtraHeaders           map[string]string
	ExtraBody              map[string]any
	PromptCache            bool
	PromptCacheMinTokens   *int
	OpencodeFreeTierCloak  *bool
	current                float64
	CooldownUntil          time.Time
	RecentFails            int
	allowedFails           int
	cooldownTime           float64
	keyMaxConsecutiveFails int
	mu                     sync.Mutex

	ewma         float64
	success      float64
	samples      int
	inflight     int
	laneInflight map[string]int
	maxInflight  int
	laneCaps     map[string]int
	alpha        float64
	adaptive     bool
}

func (d *Deployment) Name() string {
	prov := ""
	if d.Provider != nil {
		prov = d.Provider.Name
	}
	return d.Group + "/" + prov + "/" + d.Model
}

func (d *Deployment) Available(now time.Time) bool {
	if d == nil {
		return false
	}
	if now.Before(d.CooldownUntil) {
		return false
	}
	for _, k := range d.Provider.Keys {
		if k.Available(now) {
			return true
		}
	}
	return false
}

func (d *Deployment) MarkCooling(seconds float64) {
	d.CooldownUntil = time.Now().Add(time.Duration(seconds * float64(time.Second)))
}

// Inflight reports the deployment's current in-flight request count.
func (d *Deployment) Inflight() int {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.inflight
}

// P95LatencyMs reports the deployment's recent latency (EWMA, ms). The console
// renders it in the model-group table; 0 means "not measured yet".
func (d *Deployment) P95LatencyMs() float64 {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.ewma
}

// CooldownRemaining reports the seconds left before the deployment may be
// picked again (0 when it is not cooling).
func (d *Deployment) CooldownRemaining(now time.Time) float64 {
	if d.CooldownUntil.IsZero() || !now.Before(d.CooldownUntil) {
		return 0
	}
	return d.CooldownUntil.Sub(now).Seconds()
}

// Router holds model groups and drives smooth weighted round-robin + failover.
type Router struct {
	cfg       *config.Config
	providers map[string]*ProviderAccount
	groups    map[string][]*Deployment
	aliases   map[string]config.AliasEntry

	mu       sync.Mutex
	reqCount int

	numRetries             int
	allowedFails           int
	cooldownTime           float64
	failoverMode           string
	keyMaxConsecutiveFails int
	cycleEveryN            int
	strategy               string
	globalRPM, globalTPM   int
	fallbacks              map[string][]string
	contextFallbacks       map[string][]string

	healthModel string
	alpha       float64
	window      int
	adaptive    bool
	maxInflight int
	lanes       map[string]float64
	defaultLane string
	sessionTTL  time.Duration
	prefixTTL   time.Duration
	pinMu       sync.Mutex
	sessionPins map[string]pin
	prefixPins  map[string]pin
}

type pin struct {
	deployment string
	expires    time.Time
}

// New builds a Router from config.
func New(cfg *config.Config) *Router {
	r := &Router{
		cfg:                    cfg,
		providers:              map[string]*ProviderAccount{},
		groups:                 map[string][]*Deployment{},
		aliases:                map[string]config.AliasEntry{},
		numRetries:             cfg.Router.NumRetries,
		allowedFails:           cfg.Router.AllowedFails,
		cooldownTime:           cfg.Router.CooldownTime,
		failoverMode:           cfg.Router.FailoverMode,
		keyMaxConsecutiveFails: cfg.Router.KeyMaxConsecutiveFails,
		cycleEveryN:            cfg.Router.CycleEveryN,
		strategy:               cfg.Router.Strategy,
		fallbacks:              cfg.Router.Fallbacks,
		contextFallbacks:       cfg.Router.ContextWindowFallbacks,
		healthModel:            cfg.Router.HealthModel,
		alpha:                  cfg.Router.HealthEWMAAlpha,
		window:                 cfg.Router.HealthWindow,
		adaptive:               cfg.Router.AdaptiveCooldown,
		defaultLane:            cfg.Router.DefaultLane,
		sessionTTL:             time.Duration(cfg.Router.SessionAffinityTTLS * float64(time.Second)),
		prefixTTL:              time.Duration(cfg.Router.PrefixAffinityTTLS * float64(time.Second)),
		sessionPins:            map[string]pin{},
		prefixPins:             map[string]pin{},
	}
	if cfg.Router.MaxInflight != nil {
		r.maxInflight = *cfg.Router.MaxInflight
	}
	if len(cfg.Router.PriorityLanes) > 0 {
		r.lanes = cfg.Router.PriorityLanes
	}
	if r.numRetries < 0 {
		r.numRetries = 0
	}
	if r.allowedFails < 0 {
		r.allowedFails = 0
	}
	for _, p := range cfg.Providers {
		acct := &ProviderAccount{
			Name: p.Name, Type: p.ProviderType, BaseURL: p.BaseURL,
			TimeoutS: p.TimeoutS, ExtraHeaders: p.ExtraHeaders, RoundRobin: p.RoundRobin,
			AliasID: p.AliasID,
		}
		for _, k := range p.Keys {
			weight := k.Weight
			if weight < 1 {
				weight = 1
			}
			acct.Keys = append(acct.Keys, &ProviderKey{
				Label: k.Label, Secret: k.Key, Weight: weight, Enabled: k.Enabled, State: "active",
			})
		}
		r.providers[p.Name] = acct
	}
	for _, m := range cfg.ModelList {
		acct := r.providers[m.Params.Provider]
		if acct == nil {
			continue
		}
		weight := m.Params.Weight
		if weight < 1 {
			weight = 1
		}
		d := &Deployment{
			Group: m.ModelName, Model: m.Params.Model, Provider: acct, Weight: weight,
			MaxTokens: m.Params.MaxTokens, RPM: m.Params.RPM, TPM: m.Params.TPM,
			MaxInflight: m.Params.MaxInflight, Timeout: m.Params.Timeout,
			ExtraHeaders: m.Params.ExtraHeaders, ExtraBody: m.Params.ExtraBody,
			PromptCache: m.Params.PromptCache, PromptCacheMinTokens: m.Params.PromptCacheMinTokens,
			OpencodeFreeTierCloak: m.Params.OpencodeFreeTierCloak,
		}
		if m.Params.MaxInflight != nil {
			d.maxInflight = *m.Params.MaxInflight
		}
		r.groups[m.ModelName] = append(r.groups[m.ModelName], d)
		// Provider alias pooling: a provider's alias_id is an additional group
		// name that pools every deployment the alias covers.
		if acct.AliasID != "" && acct.AliasID != m.ModelName {
			r.groups[acct.AliasID] = append(r.groups[acct.AliasID], d)
		}
	}
	for name, a := range cfg.Router.ModelGroupAlias {
		r.aliases[name] = a
	}
	if cfg.Router.GlobalRPM != nil {
		r.globalRPM = *cfg.Router.GlobalRPM
	}
	if cfg.Router.GlobalTPM != nil {
		r.globalTPM = *cfg.Router.GlobalTPM
	}
	if r.sessionTTL <= 0 {
		r.sessionTTL = 600 * time.Second
	}
	if r.prefixTTL <= 0 {
		r.prefixTTL = 600 * time.Second
	}
	r.ApplyThresholds()
	return r
}

// ResolveGroup follows alias chains to a real model group.
func (r *Router) ResolveGroup(name string) string {
	seen := map[string]bool{}
	for i := 0; i < 8; i++ {
		if seen[name] {
			break
		}
		seen[name] = true
		a, ok := r.aliases[name]
		if !ok || a.Target == "" || a.Target == name {
			break
		}
		name = a.Target
	}
	return name
}

// Groups lists configured model groups.
func (r *Router) Groups() []string {
	out := make([]string, 0, len(r.groups))
	for k := range r.groups {
		out = append(out, k)
	}
	return out
}

// Deployments returns the deployments for a (resolved) group.
func (r *Router) Deployments(group string) []*Deployment {
	return r.groups[r.ResolveGroup(group)]
}

// Fallbacks returns the fallback chain for a group (resolved aliases included).
func (r *Router) Fallbacks(group string, contextOverflow bool) []string {
	m := r.fallbacks
	if contextOverflow {
		m = r.contextFallbacks
	}
	return m[group]
}

// PickDeployment selects a healthy deployment by smooth weighted round-robin.
func (r *Router) PickDeployment(group string, exclude map[string]bool) (*Deployment, error) {
	deps := r.Deployments(group)
	now := time.Now()
	r.mu.Lock()
	r.reqCount++
	advance := r.cycleEveryN > 0 && r.reqCount%r.cycleEveryN == 0
	r.mu.Unlock()

	var candidates []*Deployment
	total := 0.0
	for _, d := range deps {
		if exclude != nil && exclude[d.Name()] {
			continue
		}
		if !d.Available(now) {
			continue
		}
		if limit := r.effectiveMaxInflight(d); limit > 0 {
			d.mu.Lock()
			full := d.inflight >= limit
			d.mu.Unlock()
			if full {
				continue
			}
		}
		if r.strategy == "least-busy" {
			candidates = append(candidates, d)
			continue
		}
		w := float64(d.Weight)
		if r.healthModel == "scored" {
			w *= d.score()
			if w <= 0 {
				w = 0.0001
			}
		}
		d.mu.Lock()
		d.current += w
		candidates = append(candidates, d)
		total += w
		d.mu.Unlock()
	}
	if len(candidates) == 0 {
		return nil, fmt.Errorf("no healthy deployment for group %q", group)
	}
	switch r.strategy {
	case "least-busy":
		best := candidates[0]
		for _, d := range candidates[1:] {
			if inflight(d) < inflight(best) {
				best = d
			}
		}
		return best, nil
	case "latency-based":
		best := candidates[0]
		bestLat := ewmaOf(best)
		for _, d := range candidates[1:] {
			lat := ewmaOf(d)
			if bestLat == 0 || (lat > 0 && lat < bestLat) {
				best, bestLat = d, lat
			}
		}
		return best, nil
	}
	// smooth weighted round-robin (with optional forced cursor rotation)
	if advance && len(candidates) > 1 {
		k := r.reqCount % len(candidates)
		candidates = append(append([]*Deployment{}, candidates[k:]...), candidates[:k]...)
	}
	best := candidates[0]
	for _, d := range candidates[1:] {
		if currentOf(d) > currentOf(best) {
			best = d
		}
	}
	best.mu.Lock()
	best.current -= total
	best.mu.Unlock()
	return best, nil
}

func currentOf(d *Deployment) float64 {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.current
}

func ewmaOf(d *Deployment) float64 {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.ewma
}

// GlobalRPM / GlobalTPM expose the gateway-wide caps.
func (r *Router) GlobalRPM() int { return r.globalRPM }
func (r *Router) GlobalTPM() int { return r.globalTPM }

// FailoverStandard reports whether only 429/5xx should rotate a key.
func (r *Router) FailoverStandard() bool { return r.failoverMode == "standard" }

// CycleEveryN exposes the rotation cadence.
func (r *Router) CycleEveryN() int { return r.cycleEveryN }

func inflight(d *Deployment) int {
	d.mu.Lock()
	defer d.mu.Unlock()
	n := 0
	for _, k := range d.Provider.Keys {
		n += k.InFlight
	}
	return n
}

// PickKey selects a key from the deployment's pool by smooth WRR.
func (d *Deployment) PickKey(exclude map[string]bool) (*ProviderKey, error) {
	now := time.Now()
	d.Provider.mu.Lock()
	defer d.Provider.mu.Unlock()
	var cands []*ProviderKey
	total := 0.0
	for _, k := range d.Provider.Keys {
		if exclude != nil && exclude[k.Label] {
			continue
		}
		if !k.Available(now) {
			continue
		}
		k.current += float64(k.Weight)
		cands = append(cands, k)
		total += float64(k.Weight)
	}
	if len(cands) == 0 {
		return nil, fmt.Errorf("no healthy key for provider %q", d.Provider.Name)
	}
	best := cands[0]
	for _, k := range cands[1:] {
		if k.current > best.current {
			best = k
		}
	}
	best.current -= total
	return best, nil
}

// OnKeyResult updates key health from an attempt outcome.
func (d *Deployment) OnKeyResult(key *ProviderKey, status int, e *providers.WiwiError) {
	if key == nil {
		return
	}
	d.Provider.mu.Lock()
	if status == 200 && e == nil {
		key.MarkActive()
		d.Provider.mu.Unlock()
		d.recordHealth(key, 0, true)
		return
	}
	if e == nil {
		d.Provider.mu.Unlock()
		return
	}
	key.ErrCount++
	if e.Status == 401 || e.Status == 403 {
		key.ErrCount += 2
	}
	poolStatus := providers.StatusForKeyPool(e)
	switch {
	case e.Status == 401 || e.Status == 403:
		if e.EType != "permission_error" {
			key.MarkInvalid()
		}
	case poolStatus == 429 || poolStatus >= 500 || e.Retryable:
		key.MarkCooling(d.adaptiveCooldown())
	}
	if d.keyMaxConsecutiveFails > 0 && key.ErrCount >= d.keyMaxConsecutiveFails {
		key.MarkInvalid()
	}
	d.Provider.mu.Unlock()
	d.recordHealth(key, 0, false)
}

// OnDeploymentResult updates deployment health from an attempt outcome.
func (d *Deployment) OnDeploymentResult(status int, e *providers.WiwiError) {
	d.mu.Lock()
	defer d.mu.Unlock()
	if status == 200 && e == nil {
		d.RecentFails = 0
		return
	}
	if e == nil {
		return
	}
	if providers.StatusForKeyPool(e) != 0 || e.Retryable {
		d.RecentFails++
		if d.allowedFailsExceeded() {
			d.CooldownUntil = time.Now().Add(time.Duration(d.cooldownSeconds() * float64(time.Second)))
			d.RecentFails = 0
		}
	}
}

func (d *Deployment) allowedFailsExceeded() bool {
	// allowedFails lives on the Router; captured via package-level settings on
	// the deployment is avoided, so use the deployment's provider-independent
	// default: the router applies its own threshold in OnDeploymentResult via
	// SetAllowedFails. Kept simple here.
	return d.RecentFails >= d.allowedFails
}

// allowedFails is set by the Router when building deployments.
func (d *Deployment) setAllowedFails(n int) { d.allowedFails = n }

func (d *Deployment) cooldownSeconds() float64 {
	if d.cooldownTime > 0 {
		return d.cooldownTime
	}
	return 30
}

// adaptiveCooldown scales the base cooldown by how unhealthy the key looks
// (adaptive_cooldown). A key that is failing repeatedly is parked longer.
func (d *Deployment) adaptiveCooldown() float64 {
	base := d.cooldownSeconds()
	if !d.adaptive {
		return base
	}
	d.Provider.mu.Lock()
	rate := 0.0
	if len(d.Provider.Keys) > 0 {
		rate = d.Provider.Keys[0].success
	}
	d.Provider.mu.Unlock()
	if rate <= 0 || rate >= 1 {
		return base
	}
	// success 0.5 -> 2x, 0.1 -> 10x, capped at 10x
	mult := 1 / rate
	if mult < 1 {
		mult = 1
	}
	if mult > 10 {
		mult = 10
	}
	return base * mult
}

func (r *Router) NumRetries() int                        { return r.numRetries }
func (r *Router) Strategy() string                       { return r.strategy }
func (r *Router) SetStrategy(s string)                   { r.strategy = s }
func (r *Router) Providers() map[string]*ProviderAccount { return r.providers }

// ApplyThresholds copies router-level thresholds onto every deployment.
func (r *Router) ApplyThresholds() {
	for _, deps := range r.groups {
		for _, d := range deps {
			d.allowedFails = r.allowedFails
			d.cooldownTime = r.cooldownTime
			d.keyMaxConsecutiveFails = r.keyMaxConsecutiveFails
			d.alpha = r.alpha
			d.adaptive = r.adaptive
		}
	}
}

// ResolveAliasDisplay reports the alias entry for a requested name, if any.
func (r *Router) ResolveAliasDisplay(name string) (config.AliasEntry, bool) {
	a, ok := r.aliases[name]
	return a, ok
}

// HasGroup reports whether a (resolved) group exists.
func (r *Router) HasGroup(name string) bool {
	return len(r.groups[r.ResolveGroup(name)]) > 0
}

// GroupLabel normalizes a name for display.
func (r *Router) GroupLabel(name string) string {
	return strings.TrimSpace(r.ResolveGroup(name))
}

// ModelName exposes the provider-native model id (used by logging).
func (d *Deployment) ModelName() string { return d.Model }

// ProviderName exposes the provider account name (used by logging).
func (d *Deployment) ProviderName() string {
	if d.Provider == nil {
		return ""
	}
	return d.Provider.Name
}
