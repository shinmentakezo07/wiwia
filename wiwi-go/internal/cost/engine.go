// Package cost prices token usage against a mutable, model-keyed price table.
//
// Prices are expressed in USD per token (not per million) so a cost is a plain
// dot product: cost = prompt*Prompt + cached*Cached + completion*Completion.
package cost

import (
	"strings"
	"sync"

	"wiwi-go/internal/ir"
)

// ModelPrice is a model's per-token price in USD. Cached is the discounted
// price for prompt tokens served from the provider's prompt cache.
type ModelPrice struct {
	Prompt     float64 `json:"prompt"`
	Completion float64 `json:"completion"`
	Cached     float64 `json:"cached"`
}

// Engine holds the model price table. It is safe for concurrent use; the
// bundled fallback table is copied into a fresh Engine by NewEngine.
type Engine struct {
	mu     sync.RWMutex
	prices map[string]ModelPrice
}

// NewEngine returns an engine preloaded with the bundled fallback table.
func NewEngine() *Engine {
	e := &Engine{prices: make(map[string]ModelPrice, len(defaultPrices))}
	for model, p := range defaultPrices {
		e.prices[model] = p
	}
	return e
}

// Upsert inserts or replaces one model's price.
func (e *Engine) Upsert(model string, p ModelPrice) {
	model = normalizeModel(model)
	if model == "" {
		return
	}
	e.mu.Lock()
	e.prices[model] = p
	e.mu.Unlock()
}

// Delete removes one model's price. A missing model is a no-op.
func (e *Engine) Delete(model string) {
	model = normalizeModel(model)
	e.mu.Lock()
	delete(e.prices, model)
	e.mu.Unlock()
}

// All returns a copy of the whole table.
func (e *Engine) All() map[string]ModelPrice {
	e.mu.RLock()
	defer e.mu.RUnlock()
	out := make(map[string]ModelPrice, len(e.prices))
	for model, p := range e.prices {
		out[model] = p
	}
	return out
}

// Load merges prices into the table (an admin/persistence bulk upsert). The
// bundled fallbacks stay in place for models the caller did not supply.
func (e *Engine) Load(prices map[string]ModelPrice) {
	e.mu.Lock()
	defer e.mu.Unlock()
	for model, p := range prices {
		if m := normalizeModel(model); m != "" {
			e.prices[m] = p
		}
	}
}

// Compute prices one usage record. priced is false when the model has no
// entry, in which case cost is 0.
//
// Prompt tokens are assumed to include the cached prefix (the OpenAI shape):
// the cached slice is billed at Cached and the remainder at Prompt, but only
// when the model actually has a cached rate. Otherwise the whole prompt is
// billed at Prompt.
func (e *Engine) Compute(model string, u ir.Usage) (cost float64, priced bool) {
	p, ok := e.lookup(model)
	if !ok {
		return 0, false
	}
	prompt := clampNonNegative(u.PromptTokens)
	completion := clampNonNegative(u.CompletionTokens)
	cached := clampNonNegative(u.CachedTokens)
	if cached > prompt {
		cached = prompt
	}
	if cached > 0 && p.Cached > 0 {
		return float64(prompt-cached)*p.Prompt +
			float64(cached)*p.Cached +
			float64(completion)*p.Completion, true
	}
	return float64(prompt)*p.Prompt + float64(completion)*p.Completion, true
}

func (e *Engine) lookup(model string) (ModelPrice, bool) {
	m := normalizeModel(model)
	if m == "" {
		return ModelPrice{}, false
	}
	e.mu.RLock()
	defer e.mu.RUnlock()
	if p, ok := e.prices[m]; ok {
		return p, true
	}
	// Vendor-prefixed ids ("openai/gpt-4o", "anthropic/claude-sonnet-4") fall
	// back to the bare model name.
	if i := strings.LastIndex(m, "/"); i >= 0 && i+1 < len(m) {
		if p, ok := e.prices[m[i+1:]]; ok {
			return p, true
		}
	}
	return ModelPrice{}, false
}

func normalizeModel(model string) string {
	return strings.ToLower(strings.TrimSpace(model))
}

func clampNonNegative(n int) int {
	if n < 0 {
		return 0
	}
	return n
}
