package router

import (
	"hash/fnv"
	"strconv"
	"time"
)

// recordHealth updates EWMA latency and success-rate scoring for a key and its
// deployment (opt-in: health_model == "scored").
func (d *Deployment) recordHealth(key *ProviderKey, latencyMs int, ok bool) {
	if d.alpha <= 0 {
		return
	}
	d.mu.Lock()
	updateEWMA(&d.ewma, float64(latencyMs), d.alpha)
	updateRate(&d.success, ok, d.alpha)
	d.samples++
	d.mu.Unlock()
	if key != nil {
		d.Provider.mu.Lock()
		updateEWMA(&key.ewma, float64(latencyMs), d.alpha)
		updateRate(&key.success, ok, d.alpha)
		key.samples++
		d.Provider.mu.Unlock()
	}
}

func updateEWMA(prev *float64, sample, alpha float64) {
	if *prev == 0 {
		*prev = sample
		return
	}
	*prev = alpha*sample + (1-alpha)**prev
}

func updateRate(prev *float64, ok bool, alpha float64) {
	if *prev == 0 {
		if ok {
			*prev = 1
		} else {
			*prev = 0.0001
		}
		return
	}
	sample := 0.0
	if ok {
		sample = 1
	}
	*prev = alpha*sample + (1-alpha)**prev
}

// score is a higher-is-better health score (success rate discounted by
// latency). Only used when health_model == "scored".
func (d *Deployment) score() float64 {
	d.mu.Lock()
	defer d.mu.Unlock()
	rate := d.success
	if rate == 0 {
		rate = 0.5 // unknown: neutral, not excluded
	}
	lat := d.ewma
	if lat <= 0 {
		lat = 500
	}
	return rate * 1000 / (lat + 100)
}

// ReserveSlot admits one in-flight request against a deployment's concurrency
// ceiling (and optional priority-lane partition). Returns false when full.
func (r *Router) ReserveSlot(d *Deployment, lane string) bool {
	limit := r.effectiveMaxInflight(d)
	if limit <= 0 {
		d.mu.Lock()
		d.inflight++
		d.mu.Unlock()
		return true
	}
	if lane == "" {
		lane = r.defaultLane
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.inflight >= limit {
		return false
	}
	if cap := d.laneCaps[lane]; cap > 0 {
		if d.laneInflight[lane] >= cap {
			return false
		}
	}
	d.inflight++
	if d.laneInflight == nil {
		d.laneInflight = map[string]int{}
	}
	d.laneInflight[lane]++
	return true
}

// ReleaseSlot returns one in-flight slot.
func (r *Router) ReleaseSlot(d *Deployment, lane string) {
	if d == nil {
		return
	}
	if lane == "" {
		lane = r.defaultLane
	}
	d.mu.Lock()
	if d.inflight > 0 {
		d.inflight--
	}
	if d.laneInflight != nil && d.laneInflight[lane] > 0 {
		d.laneInflight[lane]--
	}
	d.mu.Unlock()
}

func (r *Router) effectiveMaxInflight(d *Deployment) int {
	if d.maxInflight > 0 {
		return d.maxInflight
	}
	return r.maxInflight
}

// InitLaneCaps computes per-lane concurrency partitions once max-inflight is
// known.
func (r *Router) InitLaneCaps() {
	if len(r.lanes) == 0 {
		return
	}
	for _, deps := range r.groups {
		for _, d := range deps {
			limit := r.effectiveMaxInflight(d)
			if limit <= 0 {
				continue
			}
			d.mu.Lock()
			if d.laneCaps == nil {
				d.laneCaps = map[string]int{}
			}
			for lane, share := range r.lanes {
				cap := int(float64(limit) * share)
				if cap < 1 {
					cap = 1
				}
				d.laneCaps[lane] = cap
			}
			d.mu.Unlock()
		}
	}
}

// PickDeploymentFor selects a deployment, honoring session/prefix affinity as a
// soft preference, health scoring, and the concurrency ceiling.
func (r *Router) PickDeploymentFor(group string, exclude map[string]bool, sessionID, prefix string, estTokens int) (*Deployment, error) {
	if r.healthModel == "scored" || r.maxInflight > 0 || r.lanes != nil || r.sessionAffinity || r.prefixAffinity {
		if pref := r.affinityPin(sessionID, prefix); pref != "" {
			for _, d := range r.Deployments(group) {
				now := time.Now()
				if d.Name() == pref && (exclude == nil || !exclude[pref]) && d.Available(now) && !d.RateLimited(now, estTokens) {
					return d, nil
				}
			}
		}
	}
	return r.PickDeploymentLimited(group, exclude, estTokens)
}

func (r *Router) affinityPin(sessionID, prefix string) string {
	now := time.Now()
	r.pinMu.Lock()
	defer r.pinMu.Unlock()
	if sessionID != "" && r.sessionAffinity {
		if p, ok := r.sessionPins[sessionID]; ok && now.Before(p.expires) {
			return p.deployment
		}
	}
	if prefix != "" && r.prefixAffinity {
		if p, ok := r.prefixPins[prefix]; ok && now.Before(p.expires) {
			return p.deployment
		}
	}
	return ""
}

// RecordAffinity pins a session and/or prefix to the deployment that served it.
func (r *Router) RecordAffinity(sessionID, prefix, depName string) {
	if depName == "" {
		return
	}
	now := time.Now()
	r.pinMu.Lock()
	defer r.pinMu.Unlock()
	if r.sessionPins == nil {
		r.sessionPins = map[string]pin{}
	}
	if r.prefixPins == nil {
		r.prefixPins = map[string]pin{}
	}
	if sessionID != "" && r.sessionAffinity && r.sessionTTL > 0 {
		r.sessionPins[sessionID] = pin{deployment: depName, expires: now.Add(r.sessionTTL)}
	}
	if prefix != "" && r.prefixAffinity && r.prefixTTL > 0 {
		r.prefixPins[prefix] = pin{deployment: depName, expires: now.Add(r.prefixTTL)}
	}
}

// PrefixHash derives a stable affinity key from the system prompt + tools.
func PrefixHash(system, tools string) string {
	if system == "" && tools == "" {
		return ""
	}
	h := fnv.New64a()
	h.Write([]byte(system))
	h.Write([]byte{0})
	h.Write([]byte(tools))
	return strconv.FormatUint(h.Sum64(), 16)
}
