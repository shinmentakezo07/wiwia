package providers

import (
	"strings"

	"wiwi-go/internal/ir"
)

// DeepSeek thinking-mode rules that concern the *request* side. They are a
// property of DeepSeek, not gateway policy, so every adapter that puts a
// DeepSeek model on the OpenAI wire honours them here rather than each carrying
// its own copy.
//
// When a DeepSeek model has thinking on and the request carries tools, the API
// requires the reasoning_content of *every* history assistant turn to be sent
// back, even turns whose reasoning the client (or an earlier hop) dropped.
// Without it upstream answers:
//
//	400 invalid_request_error: The reasoning_content in the thinking mode must
//	be passed back to the API.
//
// What the API validates is the field's *presence*, so a stripped turn is
// padded with an empty string. Without tools the rule does not apply: DeepSeek
// ignores history reasoning, so it is stripped rather than forwarded.

// isDeepSeekModel matches DeepSeek ids from every direction: DeepSeek's own
// catalogue (deepseek-v4-pro), a vendor-prefixed catalogue
// (deepseek-ai/DeepSeek-V3.2) and reseller aliases. Substring, case-insensitive.
func isDeepSeekModel(modelID string) bool {
	return strings.Contains(strings.ToLower(modelID), "deepseek")
}

// deepseekThinkingEnabled reports whether thinking is on. It is on by default
// for DeepSeek models, so only an explicit "none" disables it.
func deepseekThinkingEnabled(g ir.GenParams) bool {
	return g.EffectiveReasoningEffort() != "none"
}

// applyDeepSeekReasoningRoundtrip pads a missing reasoning_content on every
// history assistant message with "" so DeepSeek's tool round-trip validation
// passes. It is a no-op unless the model is DeepSeek, thinking is on and tools
// are present. Idempotent.
func applyDeepSeekReasoningRoundtrip(body map[string]any, g ir.GenParams, modelID string, hasTools bool) bool {
	if !isDeepSeekModel(modelID) || !deepseekThinkingEnabled(g) || !hasTools {
		return false
	}
	msgs, ok := body["messages"].([]any)
	if !ok {
		return false
	}
	changed := false
	for _, raw := range msgs {
		m, ok := raw.(map[string]any)
		if !ok || m["role"] != "assistant" {
			continue
		}
		if _, exists := m["reasoning_content"]; !exists {
			m["reasoning_content"] = ""
			changed = true
		}
	}
	return changed
}
