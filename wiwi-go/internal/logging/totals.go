package logging

import "sync"

// Totals accumulates process-lifetime request counters for the dashboard.
// It is safe for concurrent use.
type Totals struct {
	mu               sync.Mutex
	requests         int64
	errors           int64
	promptTokens     int64
	completionTokens int64
	cost             float64
}

// NewTotals returns zeroed totals.
func NewTotals() *Totals { return &Totals{} }

// AddRequest records one completed request.
func (t *Totals) AddRequest(prompt, completion int, cost float64, isErr bool) {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.requests++
	if isErr {
		t.errors++
	}
	if prompt > 0 {
		t.promptTokens += int64(prompt)
	}
	if completion > 0 {
		t.completionTokens += int64(completion)
	}
	t.cost += cost
}

// Snapshot returns the counters keyed requests, errors, prompt_tokens,
// completion_tokens and cost.
func (t *Totals) Snapshot() map[string]float64 {
	t.mu.Lock()
	defer t.mu.Unlock()
	return map[string]float64{
		"requests":          float64(t.requests),
		"errors":            float64(t.errors),
		"prompt_tokens":     float64(t.promptTokens),
		"completion_tokens": float64(t.completionTokens),
		"cost":              t.cost,
	}
}
