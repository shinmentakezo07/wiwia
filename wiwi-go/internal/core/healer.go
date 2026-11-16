package core

import (
	"log"
	"sync"
	"time"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/providers"
	"wiwi-go/internal/router"
)

// Healer is the opt-in background prober: it sends 1-token requests to sick
// keys/deployments and restores them into rotation on success. Probes spend
// real provider money, so it is off by default.
type Healer struct {
	Router        *router.Router
	Tick          time.Duration
	ProbeTimeoutS float64
	MaxProbes     int
	// ProbeBackoffBase/Cap scale the per-key probe interval after repeated
	// failures so a permanently dead key is not re-probed on every sweep.
	ProbeBackoffBase time.Duration
	ProbeBackoffCap  time.Duration
	stop             chan struct{}
	done             chan struct{}

	mu        sync.Mutex
	failures  map[string]int
	nextProbe map[string]time.Time
}

func NewHealer(rt *router.Router, tickS, probeTimeoutS float64, maxProbes int) *Healer {
	if tickS <= 0 {
		tickS = 30
	}
	if probeTimeoutS <= 0 {
		probeTimeoutS = 10
	}
	if maxProbes <= 0 {
		maxProbes = 8
	}
	return &Healer{
		Router: rt, Tick: time.Duration(tickS * float64(time.Second)),
		ProbeTimeoutS: probeTimeoutS, MaxProbes: maxProbes,
		ProbeBackoffBase: 30 * time.Second,
		ProbeBackoffCap:  30 * time.Minute,
		stop:             make(chan struct{}),
		done:             make(chan struct{}),
		failures:         map[string]int{},
		nextProbe:        map[string]time.Time{},
	}
}

func (h *Healer) Start() {
	go func() {
		defer close(h.done)
		ticker := time.NewTicker(h.Tick)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				h.Sweep()
			case <-h.stop:
				return
			}
		}
	}()
}

func (h *Healer) Stop() {
	select {
	case <-h.stop:
	default:
		close(h.stop)
	}
	<-h.done
}

// Sweep probes up to MaxProbes sick keys, honouring per-key backoff.
func (h *Healer) Sweep() {
	now := time.Now()
	probes := 0
	for name, acct := range h.Router.Providers() {
		if probes >= h.MaxProbes {
			return
		}
		var dep *router.Deployment
		for _, d := range h.Router.Deployments(name) {
			dep = d
			break
		}
		if dep == nil {
			// provider name may not be a model group; find any deployment using it
			for _, g := range h.Router.Groups() {
				for _, d := range h.Router.Deployments(g) {
					if d.Provider == acct {
						dep = d
						break
					}
				}
				if dep != nil {
					break
				}
			}
		}
		if dep == nil {
			continue
		}
		for _, key := range acct.Keys {
			if probes >= h.MaxProbes {
				return
			}
			if key.Available(now) {
				continue
			}
			id := name + "/" + key.Label
			if !h.due(id, now) {
				continue
			}
			probes++
			if h.probe(dep, key) {
				h.onSuccess(id)
				key.MarkActive()
				log.Printf("wiwi: healer restored key %s/%s", name, key.Label)
			} else {
				h.onFailure(id, now)
			}
		}
	}
}

func (h *Healer) due(id string, now time.Time) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	next, ok := h.nextProbe[id]
	return !ok || !now.Before(next)
}

func (h *Healer) onSuccess(id string) {
	h.mu.Lock()
	delete(h.failures, id)
	delete(h.nextProbe, id)
	h.mu.Unlock()
}

func (h *Healer) onFailure(id string, now time.Time) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.failures[id]++
	backoff := h.ProbeBackoffBase
	for i := 1; i < h.failures[id]; i++ {
		if backoff >= h.ProbeBackoffCap {
			break
		}
		backoff *= 2
	}
	if h.ProbeBackoffCap > 0 && backoff > h.ProbeBackoffCap {
		backoff = h.ProbeBackoffCap
	}
	if backoff <= 0 {
		backoff = 30 * time.Second
	}
	h.nextProbe[id] = now.Add(backoff)
}

func (h *Healer) probe(dep *router.Deployment, key *router.ProviderKey) bool {
	adapter, err := providers.FreshAdapter(dep.Provider.Type)
	if err != nil {
		return false
	}
	req := ir.Request{
		Model:    dep.Model,
		Messages: []ir.Message{{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}}},
	}
	maxTok := 1
	req.GenParams.MaxTokens = &maxTok
	body, err := adapter.EncodeRequest(req, dep.Model, nil)
	if err != nil {
		return false
	}
	headers := adapter.Headers(providers.ProviderKeyRef{Label: key.Label, Secret: key.Secret})
	for k, v := range dep.Provider.ExtraHeaders {
		headers[k] = v
	}
	resp, status, err := providers.DoRequest(
		adapter.BuildURL(dep.Provider.BaseURL, dep.Model, false), headers, body, h.ProbeTimeoutS)
	if err != nil || status != 200 {
		return false
	}
	_, derr := adapter.DecodeResponse(status, resp)
	return derr == nil
}
