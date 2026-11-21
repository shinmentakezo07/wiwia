package logging

import "sync"

// RequestStats is one completed request's contribution to the process-lifetime
// totals. The Prometheus exporter renders these as counters, so they must never
// be derived from a bounded event ring: a ring-derived count decreases on every
// eviction, which PromQL reads as a counter reset.
type RequestStats struct {
	Prompt        int
	Completion    int
	Cached        int
	CacheCreation int
	Reasoning     int
	Cost          float64
	IsErr         bool
	// PromptCacheHit counts a provider prompt-cache hit; ResponseCacheHit a
	// wiwi exact-match response-cache hit.
	PromptCacheHit   bool
	ResponseCacheHit bool
	// Estimated marks token counts wiwi filled in because upstream omitted
	// usage.
	Estimated bool
	// StreamError counts a stream that failed after it started.
	StreamError bool
}

// Totals accumulates process-lifetime request counters for the dashboard and
// the Prometheus exporter. It is safe for concurrent use.
type Totals struct {
	mu               sync.Mutex
	requests         int64
	errors           int64
	promptTokens     int64
	completionTokens int64
	cachedTokens     int64
	cacheCreation    int64
	reasoningTokens  int64
	cost             float64
	promptCacheHits  int64
	respCacheHits    int64
	usageEstimated   int64
	streamErrors     int64
}

// NewTotals returns zeroed totals.
func NewTotals() *Totals { return &Totals{} }

// AddRequest records one completed request.
func (t *Totals) AddRequest(s RequestStats) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.requests++
	if s.IsErr {
		t.errors++
	}
	if s.Prompt > 0 {
		t.promptTokens += int64(s.Prompt)
	}
	if s.Completion > 0 {
		t.completionTokens += int64(s.Completion)
	}
	if s.Cached > 0 {
		t.cachedTokens += int64(s.Cached)
	}
	if s.CacheCreation > 0 {
		t.cacheCreation += int64(s.CacheCreation)
	}
	if s.Reasoning > 0 {
		t.reasoningTokens += int64(s.Reasoning)
	}
	t.cost += s.Cost
	if s.PromptCacheHit {
		t.promptCacheHits++
	}
	if s.ResponseCacheHit {
		t.respCacheHits++
	}
	if s.Estimated {
		t.usageEstimated++
	}
	if s.StreamError {
		t.streamErrors++
	}
}

// Snapshot returns the counters used by the dashboard, alert rules and the
// Prometheus exporter.
func (t *Totals) Snapshot() map[string]float64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	return map[string]float64{
		"requests":              float64(t.requests),
		"errors":                float64(t.errors),
		"prompt_tokens":         float64(t.promptTokens),
		"completion_tokens":     float64(t.completionTokens),
		"cached_tokens":         float64(t.cachedTokens),
		"cache_creation_tokens": float64(t.cacheCreation),
		"reasoning_tokens":      float64(t.reasoningTokens),
		"cost":                  t.cost,
		"prompt_cache_hits":     float64(t.promptCacheHits),
		"response_cache_hits":   float64(t.respCacheHits),
		"usage_estimated":       float64(t.usageEstimated),
		"stream_errors":         float64(t.streamErrors),
	}
}
