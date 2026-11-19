package providers

import (
	"encoding/json"
	"math"
	"strings"
	"wiwi-go/internal/streaming"

	"wiwi-go/internal/ir"
)

// Shared, total coercion helpers for the outbound adapters. Every upstream
// read on the decode paths goes through one of these: a JSON null or a
// typed-wrong value must degrade to the empty shape, never panic mid-stream.

// tokenCount turns a usage counter into the int the IR requires.
func tokenCount(v any) int {
	switch x := v.(type) {
	case int:
		return x
	case int32:
		return int(x)
	case int64:
		return int(x)
	case float64:
		if math.IsNaN(x) || math.IsInf(x, 0) {
			return 0
		}
		return int(x)
	case float32:
		return int(x)
	case json.Number:
		if i, err := x.Int64(); err == nil {
			return int(i)
		}
		if f, err := x.Float64(); err == nil && !math.IsNaN(f) {
			return int(f)
		}
	}
	return 0
}

// asIntValue reports a JSON numeric as an int (integral floats included).
// A bool is not a number.
func asIntValue(v any) (int, bool) {
	switch x := v.(type) {
	case int:
		return x, true
	case int64:
		return int(x), true
	case float64:
		if math.IsNaN(x) || math.IsInf(x, 0) {
			return 0, false
		}
		return int(x), true
	case json.Number:
		if i, err := x.Int64(); err == nil {
			return int(i), true
		}
	}
	return 0, false
}

func mapStr(m map[string]any, key string) string {
	if m == nil {
		return ""
	}
	return AsStr(m[key], "")
}

func mapInt(m map[string]any, key string) int {
	if m == nil {
		return 0
	}
	return tokenCount(m[key])
}

// setDefault writes k only when the body has not already claimed it, so a
// caller-supplied extra never overrides a modelled field.
func setDefault(body map[string]any, k string, v any) {
	if k == "" {
		return
	}
	if _, ok := body[k]; !ok {
		body[k] = v
	}
}

// mergeDefaults merges src into body without overriding existing keys.
func mergeDefaults(body, src map[string]any) {
	for k, v := range src {
		setDefault(body, k, v)
	}
}

// normalizeFinishReason maps an upstream OpenAI finish_reason onto an IR
// StopReason. Total by construction: non-string and unknown spellings both
// yield "stop".
func normalizeFinishReason(raw any) ir.StopReason {
	s, ok := raw.(string)
	if !ok {
		return ir.StopStop
	}
	switch s {
	case "stop":
		return ir.StopStop
	case "length":
		return ir.StopLength
	case "tool_calls", "function_call", "tool_use":
		return ir.StopToolCall
	case "content_filter":
		return ir.StopContentFilter
	case "max_tokens":
		return ir.StopLength
	case "end_turn", "error":
		return ir.StopStop
	case "stop_sequence":
		return ir.StopStopSequence
	case "pause_turn":
		return ir.StopPauseTurn
	case "context_window_exceeded", "model_context_window_exceeded":
		return ir.StopContextWindowExceeded
	case "compaction":
		return ir.StopCompaction
	}
	return ir.StopStop
}

// anthropicStopReason maps the Messages API stop_reason vocabulary.
func anthropicStopReason(raw any) ir.StopReason {
	s, ok := raw.(string)
	if !ok {
		return ir.StopStop
	}
	switch s {
	case "end_turn":
		return ir.StopStop
	case "max_tokens":
		return ir.StopLength
	case "tool_use":
		return ir.StopToolCall
	case "stop_sequence":
		return ir.StopStopSequence
	case "refusal":
		return ir.StopContentFilter
	case "pause_turn":
		return ir.StopPauseTurn
	case "model_context_window_exceeded":
		return ir.StopContextWindowExceeded
	case "compaction":
		return ir.StopCompaction
	}
	return ir.StopStop
}

// geminiStopReason maps the Gemini finishReason vocabulary.
func geminiStopReason(raw any) ir.StopReason {
	s, ok := raw.(string)
	if !ok {
		return ir.StopStop
	}
	switch s {
	case "STOP":
		return ir.StopStop
	case "MAX_TOKENS":
		return ir.StopLength
	case "SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED", "SPII":
		return ir.StopContentFilter
	}
	return ir.StopStop
}

// argsFromAny decodes tool-call arguments that may arrive as an object or as
// a JSON string. It returns the parsed object plus the raw spelling, and never
// fails: unparseable input degrades to an empty object.
func argsFromAny(v any) (map[string]any, string) {
	switch x := v.(type) {
	case map[string]any:
		raw, _ := json.Marshal(x)
		return x, string(raw)
	case string:
		if strings.TrimSpace(x) == "" {
			return map[string]any{}, ""
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(x), &m); err == nil && m != nil {
			return m, x
		}
		// Truncated/invalid JSON: repair rather than drop every argument.
		if repaired := streaming.RepairArgs(x); len(repaired) > 0 {
			return repaired, x
		}
		return map[string]any{}, x
	}
	return map[string]any{}, ""
}

// textPartsOf joins the text parts of a message.
func textPartsOf(m ir.Message) string {
	var parts []string
	for _, p := range m.Parts {
		if p.Type == ir.PartText {
			if s := p.Str("text"); s != "" {
				parts = append(parts, s)
			}
		}
	}
	return strings.Join(parts, "\n")
}

// imageURL resolves the OpenAI “image_url“ spelling for an IR image part.
func imageURL(p ir.Part) string {
	if url := p.Str("url"); url != "" {
		return url
	}
	b64 := p.Str("b64")
	if b64 == "" {
		return ""
	}
	mime := p.Str("mime")
	if mime == "" {
		mime = "image/png"
	}
	return "data:" + mime + ";base64," + b64
}

// openToolState is the per-index state a streaming decoder accumulates for one
// open tool call.
type openToolState struct {
	ID   string
	Name string
}

// sortedToolIndices returns the open indices in a deterministic order.
func sortedToolIndices(m map[int]*openToolState) []int {
	out := make([]int, 0, len(m))
	for idx := range m {
		out = append(out, idx)
	}
	for i := 1; i < len(out); i++ {
		for j := i; j > 0 && out[j] < out[j-1]; j-- {
			out[j], out[j-1] = out[j-1], out[j]
		}
	}
	return out
}

// supportsReasoningEffort reports whether a provider type accepts the OpenAI
// “reasoning_effort“ field. Strict OpenAI-compatible gateways reject it with
// a 400, so it only goes to native OpenAI-shaped endpoints.
func supportsReasoningEffort(providerType string) bool {
	switch providerType {
	case "openai-compatible", "gmicloud", "nvidia-nim", "bai", "openrouter":
		return false
	}
	return true
}

// clientSentThinking mirrors Python's _client_sent_thinking: any explicit
// reasoning knob suppresses Cline's xhigh default.
func clientSentThinking(g ir.GenParams) bool {
	return (g.ReasoningEffort != nil && *g.ReasoningEffort != "") ||
		g.ThinkingBudget != nil ||
		(g.ThinkingType != nil && *g.ThinkingType != "")
}

// emitPerMessageReasoning reports whether history assistant messages may carry
// reasoning_content. Strict gateways reject the field on input.
func emitPerMessageReasoning(providerType string) bool {
	switch providerType {
	case "openai-compatible", "gmicloud", "openrouter", "bai", "nvidia-nim":
		return false
	}
	return true
}
