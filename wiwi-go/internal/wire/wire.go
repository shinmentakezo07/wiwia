// Package wire implements the inbound wire codecs. Inbound JSON request bodies
// decode into the canonical IR (internal/ir), IR turns re-encode into each
// dialect's non-streaming response body, and streaming deltas
// (internal/streaming) encode into SSE `data:` payload strings.
//
// Four dialects share one hub: OpenAI Chat, OpenAI Responses, Anthropic
// Messages and the legacy OpenAI Completions surface. Every encoder returns
// payload strings only — the server adds the `data: ` prefix, the trailing
// blank line, and (for Messages) the `event:` line derived from the JSON
// `type` field.
package wire

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

// Surface names an inbound dialect.
type Surface string

const (
	SurfaceChat        Surface = "chat"
	SurfaceResponses   Surface = "responses"
	SurfaceMessages    Surface = "messages"
	SurfaceCompletions Surface = "completions"
)

// DecodeRequest decodes an inbound JSON body into the canonical IR. A returned
// error is a client error (400).
func DecodeRequest(s Surface, body map[string]any) (ir.Request, error) {
	if body == nil {
		return ir.Request{}, fmt.Errorf("request body must be a JSON object")
	}
	switch s {
	case SurfaceChat:
		return decodeChat(body)
	case SurfaceResponses:
		return decodeResponses(body)
	case SurfaceMessages:
		return decodeMessages(body)
	case SurfaceCompletions:
		return decodeCompletions(body)
	}
	return ir.Request{}, fmt.Errorf("unknown surface %q", s)
}

// EncodeResponse encodes a non-streaming IR turn into the dialect's response
// body.
func EncodeResponse(s Surface, turn ir.AssistantTurn, model string, req ir.Request) map[string]any {
	switch s {
	case SurfaceChat:
		return encodeChatResponse(turn, model)
	case SurfaceResponses:
		return encodeResponsesResponse(turn, model)
	case SurfaceMessages:
		return encodeMessagesResponse(turn, model)
	case SurfaceCompletions:
		return encodeCompletionsResponse(turn, model)
	}
	_, body := ErrorBody(s, 500, "api_error", fmt.Sprintf("unknown surface %q", s))
	return body
}

// StreamEncoder turns IR streaming deltas into SSE data payload strings.
// Start is called once before any delta; End is called once after the last
// delta. Returned strings never include the `data: ` prefix or the trailing
// blank line.
type StreamEncoder interface {
	Start() []string
	Encode(d streaming.Delta) []string
	End() []string
}

// NewStreamEncoder returns the stateful encoder for a surface. An unknown
// surface falls back to the Chat encoder so a caller can never be handed nil.
func NewStreamEncoder(s Surface, model, reqID string) StreamEncoder {
	switch s {
	case SurfaceResponses:
		return newResponsesStreamEncoder(model, reqID)
	case SurfaceMessages:
		return newMessagesStreamEncoder(model, reqID)
	case SurfaceCompletions:
		return newCompletionsStreamEncoder(model, reqID)
	}
	return newChatStreamEncoder(model, reqID)
}

// ErrorBody renders a dialect-shaped error envelope. The status is returned
// unchanged (normalized to 500 when unset).
func ErrorBody(s Surface, status int, eType, message string) (int, map[string]any) {
	if status <= 0 {
		status = 500
	}
	switch s {
	case SurfaceMessages:
		return status, map[string]any{
			"type":  "error",
			"error": map[string]any{"type": anthropicErrorType(status, eType), "message": message},
		}
	case SurfaceResponses:
		if eType == "" {
			eType = "api_error"
		}
		return status, map[string]any{
			"error": map[string]any{
				"message": message, "type": eType, "code": eType, "param": nil,
			},
		}
	}
	if eType == "" {
		eType = "api_error"
	}
	return status, map[string]any{
		"error": map[string]any{"message": message, "type": eType, "code": eType},
	}
}

// anthropicErrorType maps wiwi's vocabulary (or a bare status) onto Anthropic's
// error type vocabulary.
func anthropicErrorType(status int, eType string) string {
	if mapped, ok := anthropicErrorTypes[eType]; ok {
		return mapped
	}
	if mapped, ok := anthropicErrorTypeByStatus[status]; ok {
		return mapped
	}
	return "api_error"
}

var anthropicErrorTypes = map[string]string{
	"authentication_error":     "authentication_error",
	"permission_error":         "permission_error",
	"rate_limit_error":         "rate_limit_error",
	"invalid_request_error":    "invalid_request_error",
	"not_found_error":          "not_found_error",
	"api_error":                "api_error",
	"service_unavailable":      "overloaded_error",
	"overloaded_error":         "overloaded_error",
	"billing_error":            "billing_error",
	"invalid_tool_use":         "invalid_tool_use",
	"request_too_large":        "request_too_large",
	"timeout_error":            "timeout_error",
	"api_connection_error":     "api_error",
	"timeout":                  "timeout_error",
	"budget_exceeded":          "permission_error",
	"context_window_exceeded":  "invalid_request_error",
	"content_policy_violation": "invalid_request_error",
}

var anthropicErrorTypeByStatus = map[int]string{
	400: "invalid_request_error",
	401: "authentication_error",
	403: "permission_error",
	404: "not_found_error",
	413: "request_too_large",
	429: "rate_limit_error",
	504: "timeout_error",
	529: "overloaded_error",
}

// --- shared decoding helpers ------------------------------------------------

// coerceInt accepts a JSON integer (including a whole float or json.Number) and
// rejects bools, strings, objects and non-integral numbers.
func coerceInt(v any) (int, bool) {
	switch n := v.(type) {
	case int:
		return n, true
	case int32:
		return int(n), true
	case int64:
		return int(n), true
	case float64:
		if math.IsNaN(n) || math.IsInf(n, 0) || n != math.Trunc(n) {
			return 0, false
		}
		return int(n), true
	case json.Number:
		if i, err := n.Int64(); err == nil {
			return int(i), true
		}
		if f, err := n.Float64(); err == nil && f == math.Trunc(f) {
			return int(f), true
		}
	}
	return 0, false
}

// coerceIntLoose additionally accepts a digit string ("77"), which the
// Anthropic surface tolerates for max_tokens/thinking budgets.
func coerceIntLoose(v any) (int, bool) {
	if s, ok := v.(string); ok {
		s = strings.TrimSpace(s)
		if s == "" {
			return 0, false
		}
		if i, err := strconv.Atoi(s); err == nil {
			return i, true
		}
		return 0, false
	}
	return coerceInt(v)
}

// coerceFloat accepts a JSON number and rejects bools, strings and non-finite
// values.
func coerceFloat(v any) (float64, bool) {
	switch n := v.(type) {
	case float64:
		if math.IsNaN(n) || math.IsInf(n, 0) {
			return 0, false
		}
		return n, true
	case int:
		return float64(n), true
	case int64:
		return float64(n), true
	case json.Number:
		f, err := n.Float64()
		if err != nil || math.IsNaN(f) || math.IsInf(f, 0) {
			return 0, false
		}
		return f, true
	}
	return 0, false
}

func floatPtr(v any) *float64 {
	if f, ok := coerceFloat(v); ok {
		return &f
	}
	return nil
}

func intPtr(v any) *int {
	if i, ok := coerceInt(v); ok {
		return &i
	}
	return nil
}

func boolPtr(v any) *bool {
	if b, ok := v.(bool); ok {
		return &b
	}
	return nil
}

func strPtr(v any) *string {
	if s, ok := v.(string); ok {
		return &s
	}
	return nil
}

// strOrEmpty coerces a decoded text field, mapping junk to "".
func strOrEmpty(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

// stopList normalizes a wire `stop` field to []string. A bare string is ONE
// sequence; non-string items in a list are dropped.
func stopList(v any) []string {
	switch s := v.(type) {
	case string:
		if s == "" {
			return nil
		}
		return []string{s}
	case []any:
		out := make([]string, 0, len(s))
		for _, item := range s {
			if str, ok := item.(string); ok {
				out = append(out, str)
			}
		}
		if len(out) == 0 {
			return nil
		}
		return out
	case []string:
		return s
	}
	return nil
}

// carryExtras returns every body key not in the modelled set.
func carryExtras(body map[string]any, known map[string]struct{}) map[string]any {
	extras := make(map[string]any)
	for k, v := range body {
		if _, ok := known[k]; ok {
			continue
		}
		extras[k] = v
	}
	if len(extras) == 0 {
		return nil
	}
	return extras
}

// pickExtras returns the body keys present in an allowlist (the Anthropic
// surface forwards a known set verbatim rather than the unknown remainder).
func pickExtras(body map[string]any, allow map[string]struct{}) map[string]any {
	extras := make(map[string]any)
	for k := range allow {
		if v, ok := body[k]; ok {
			extras[k] = v
		}
	}
	if len(extras) == 0 {
		return nil
	}
	return extras
}

// parseDataURL splits `data:<mime>;base64,<payload>`.
func parseDataURL(s string) (mime, b64 string, ok bool) {
	if !strings.HasPrefix(s, "data:") {
		return "", "", false
	}
	header, payload, found := strings.Cut(s, ",")
	if !found {
		return "", "", false
	}
	mime = strings.TrimPrefix(header, "data:")
	if i := strings.IndexByte(mime, ';'); i >= 0 {
		mime = mime[:i]
	}
	return mime, payload, true
}

// decodeArgs resolves a tool-call `arguments` field (string or object) into the
// parsed map plus the raw string form. Truncated JSON is repaired best-effort;
// unparseable args become an empty object.
func decodeArgs(raw any) (map[string]any, string) {
	switch a := raw.(type) {
	case map[string]any:
		return a, mustJSON(a)
	case string:
		if strings.TrimSpace(a) == "" {
			return map[string]any{}, a
		}
		if m := parseJSONObject(a); m != nil {
			return m, a
		}
		if repaired, ok := repairJSON(a); ok {
			if m := parseJSONObject(repaired); m != nil {
				return m, a
			}
		}
		return map[string]any{}, a
	}
	return map[string]any{}, ""
}

func parseJSONObject(s string) map[string]any {
	var m map[string]any
	if err := json.Unmarshal([]byte(s), &m); err != nil {
		return nil
	}
	return m
}

// repairJSON closes unterminated strings and containers in a truncated JSON
// fragment (a provider cut off mid-tool-call).
func repairJSON(s string) (string, bool) {
	s = strings.TrimSpace(s)
	if s == "" {
		return "", false
	}
	var stack []byte
	inStr, escaped := false, false
	for i := 0; i < len(s); i++ {
		c := s[i]
		if inStr {
			switch {
			case escaped:
				escaped = false
			case c == '\\':
				escaped = true
			case c == '"':
				inStr = false
			}
			continue
		}
		switch c {
		case '"':
			inStr = true
		case '{':
			stack = append(stack, '}')
		case '[':
			stack = append(stack, ']')
		case '}', ']':
			if n := len(stack); n > 0 && stack[n-1] == c {
				stack = stack[:n-1]
			}
		}
	}
	var b strings.Builder
	b.WriteString(s)
	if inStr {
		b.WriteByte('"')
	}
	for i := len(stack) - 1; i >= 0; i-- {
		b.WriteByte(stack[i])
	}
	return b.String(), true
}

// mustJSON marshals a value to a JSON string, falling back to "{}" so an
// encoder can never emit a malformed frame.
func mustJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return "{}"
	}
	return string(b)
}

func unixNow() int64 { return time.Now().Unix() }

// respID mints an id shaped like the vendor's (`chatcmpl-<hex>`).
func respID(prefix string) string {
	var b [12]byte
	if _, err := rand.Read(b[:]); err == nil {
		return prefix + hex.EncodeToString(b[:])
	}
	return prefix + strconv.FormatInt(time.Now().UnixNano(), 16)
}

// --- finish-reason mapping --------------------------------------------------

// openAIFinish maps an IR stop reason onto the OpenAI finish_reason vocabulary.
func openAIFinish(r ir.StopReason) string {
	switch r {
	case ir.StopStop:
		return "stop"
	case ir.StopLength:
		return "length"
	case ir.StopToolCall:
		return "tool_calls"
	case ir.StopContentFilter:
		return "content_filter"
	case ir.StopContextWindowExceeded:
		return "length"
	}
	return "stop"
}

// anthropicStop maps an IR stop reason onto the Anthropic stop_reason
// vocabulary.
func anthropicStop(r ir.StopReason) string {
	switch r {
	case ir.StopLength:
		return "max_tokens"
	case ir.StopToolCall:
		return "tool_use"
	case ir.StopContentFilter:
		return "refusal"
	case ir.StopPauseTurn:
		return "pause_turn"
	case ir.StopStopSequence:
		return "stop_sequence"
	case ir.StopContextWindowExceeded:
		return "model_context_window_exceeded"
	case ir.StopCompaction:
		return "compaction"
	}
	return "end_turn"
}

// toolFinishValid reports whether a tool_call stop is legal given the number of
// client-visible tool calls the encoder emitted.
func toolFinishValid(r ir.StopReason, emittedCalls int) bool {
	if r != ir.StopToolCall {
		return true
	}
	return emittedCalls > 0
}

// --- usage helpers ----------------------------------------------------------

// openAIUsage renders usage in the OpenAI convention, where prompt_tokens
// INCLUDES cached tokens.
func openAIUsage(u ir.Usage) map[string]any {
	prompt := u.PromptTokens
	if u.CachedInPrompt == nil || !*u.CachedInPrompt {
		prompt += u.CachedTokens
	}
	return map[string]any{
		"prompt_tokens":             prompt,
		"completion_tokens":         u.CompletionTokens,
		"total_tokens":              prompt + u.CompletionTokens,
		"prompt_tokens_details":     map[string]any{"cached_tokens": u.CachedTokens},
		"completion_tokens_details": map[string]any{"reasoning_tokens": u.ReasoningTokens},
	}
}

// openAIStreamUsage renders a buffered UsageFinal in the OpenAI convention.
func openAIStreamUsage(u streaming.UsageFinal) map[string]any {
	prompt := u.Prompt
	if u.CachedInPrompt == nil || !*u.CachedInPrompt {
		prompt += u.Cached
	}
	return map[string]any{
		"prompt_tokens":             prompt,
		"completion_tokens":         u.Output,
		"total_tokens":              prompt + u.Output,
		"prompt_tokens_details":     map[string]any{"cached_tokens": u.Cached},
		"completion_tokens_details": map[string]any{"reasoning_tokens": u.Reasoning},
	}
}

// responsesUsage renders the Responses surface usage block.
func responsesUsage(prompt, output, cached, reasoning int) map[string]any {
	return map[string]any{
		"input_tokens":          prompt,
		"output_tokens":         output,
		"total_tokens":          prompt + output,
		"input_tokens_details":  map[string]any{"cached_tokens": cached},
		"output_tokens_details": map[string]any{"reasoning_tokens": reasoning},
	}
}

// anthropicUsage renders the Messages usage block. input_tokens EXCLUDES cache
// reads, so an OpenAI-shaped upstream total is re-based.
func anthropicUsage(prompt, output, cached, cacheCreation, reasoning int, cachedInPrompt bool) map[string]any {
	input := prompt
	if cachedInPrompt {
		input -= cached
	}
	if input < 0 {
		input = 0
	}
	usage := map[string]any{
		"input_tokens":                input,
		"output_tokens":               output,
		"cache_read_input_tokens":     cached,
		"cache_creation_input_tokens": cacheCreation,
	}
	if reasoning > 0 {
		usage["output_tokens_details"] = map[string]any{"thinking_tokens": reasoning}
	}
	return usage
}

// --- deferred prose buffer --------------------------------------------------

// maxDeferredChars bounds prose buffered while a tool block is open. Newest
// content wins on overflow.
const maxDeferredChars = 256 * 1024

type deferredItem struct {
	kind string // "text" | "thinking"
	text string
}

type deferredBuffer struct {
	items []deferredItem
	chars int
}

func (d *deferredBuffer) add(kind, text string) {
	if text == "" {
		return
	}
	if len(text) > maxDeferredChars {
		text = text[len(text)-maxDeferredChars:]
	}
	d.items = append(d.items, deferredItem{kind: kind, text: text})
	d.chars += len(text)
	for d.chars > maxDeferredChars && len(d.items) > 1 {
		d.chars -= len(d.items[0].text)
		d.items = d.items[1:]
	}
}

func (d *deferredBuffer) drain() []deferredItem {
	if len(d.items) == 0 {
		return nil
	}
	out := d.items
	d.items = nil
	d.chars = 0
	return out
}
