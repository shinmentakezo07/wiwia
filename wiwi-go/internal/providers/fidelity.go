package providers

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"sync"

	"wiwi-go/internal/ir"
)

// Provider-specific wire logic ported from the Python adapters.

// -- OpenRouter reasoning ---------------------------------------------------

// applyOpenRouterReasoning replaces OpenAI's reasoning_effort with OpenRouter's
// unified `reasoning` object and reconciles the completion cap.
func applyOpenRouterReasoning(body map[string]any, g ir.GenParams) {
	delete(body, "reasoning_effort")
	effort := g.EffectiveReasoningEffort()
	var obj map[string]any
	switch {
	case effort == "none":
		obj = map[string]any{"enabled": false}
	case g.ThinkingType != nil && *g.ThinkingType == "disabled":
		obj = map[string]any{"enabled": false}
	case g.ThinkingBudget != nil:
		b := *g.ThinkingBudget
		if b < 1024 {
			b = 1024
		}
		obj = map[string]any{"max_tokens": b}
		if mt, ok := body["max_tokens"].(int); ok && mt <= b {
			body["max_tokens"] = b + 1024
		}
	case effort != "":
		obj = map[string]any{"effort": effort}
	}
	if obj != nil {
		body["reasoning"] = obj
	}
}

// stripHistoryReasoning removes per-message reasoning fields OpenRouter rejects
// on input (it uses reasoning_details instead).
func stripHistoryReasoning(body map[string]any) {
	msgs, _ := body["messages"].([]any)
	for _, raw := range msgs {
		if m, ok := raw.(map[string]any); ok {
			delete(m, "reasoning")
			delete(m, "reasoning_content")
		}
	}
}

// decodeOpenRouterReasoning extracts reasoning from a message: the string form
// plus the structured reasoning_details array.
func decodeOpenRouterReasoning(message map[string]any) []ir.ThinkingPart {
	var out []ir.ThinkingPart
	if s, _ := message["reasoning"].(string); s != "" {
		out = append(out, ir.ThinkingPart{Text: s, BlockType: "thinking"})
	} else if s, _ := message["reasoning_content"].(string); s != "" {
		out = append(out, ir.ThinkingPart{Text: s, BlockType: "thinking"})
	}
	for _, raw := range AsList(message["reasoning_details"]) {
		rd, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		rtype, _ := rd["type"].(string)
		switch rtype {
		case "reasoning.text":
			text, _ := rd["text"].(string)
			sig, _ := rd["signature"].(string)
			out = append(out, ir.ThinkingPart{Text: text, Signature: sig, BlockType: "thinking"})
		case "reasoning.summary":
			summary, _ := rd["summary"].(string)
			out = append(out, ir.ThinkingPart{Text: summary, BlockType: "thinking"})
		case "reasoning.encrypted":
			data, _ := rd["data"].(string)
			out = append(out, ir.ThinkingPart{BlockType: "redacted_thinking", Data: data})
		}
	}
	return out
}

// -- NVIDIA NIM tool-schema sanitization ------------------------------------

var nimSchemaValueKeys = map[string]bool{
	"additionalProperties": true, "not": true, "contains": true,
	"propertyNames": true, "if": true, "then": true, "else": true, "items": true,
}
var nimSchemaListKeys = map[string]bool{"allOf": true, "anyOf": true, "oneOf": true, "prefixItems": true}
var nimSchemaMapKeys = map[string]bool{
	"properties": true, "patternProperties": true, "$defs": true,
	"definitions": true, "dependentSchemas": true,
}

const nimAliasPrefix = "_nim_arg_"

// sanitizeNIMTools strips boolean subschemas, aliases unsafe parameter names,
// and returns the alias map (tool name -> alias -> original) for reversal.
func sanitizeNIMTools(tools []any) ([]any, map[string]map[string]string) {
	// Deep-copy first: sanitizeNIMSchema mutates in place, and the caller's
	// tools (and the memo key) must stay stable across calls.
	if b, err := json.Marshal(tools); err == nil {
		var copyTools []any
		if json.Unmarshal(b, &copyTools) == nil {
			tools = copyTools
		}
	}
	aliases := map[string]map[string]string{}
	for _, raw := range tools {
		t, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		fn, _ := t["function"].(map[string]any)
		if fn == nil {
			continue
		}
		name, _ := fn["name"].(string)
		params, _ := fn["parameters"].(map[string]any)
		if params == nil {
			continue
		}
		toolAliases := map[string]string{}
		sanitizeNIMSchema(params, toolAliases)
		if len(toolAliases) > 0 {
			aliases[name] = toolAliases
		}
	}
	return tools, aliases
}

func sanitizeNIMSchema(node map[string]any, aliases map[string]string) {
	for key, val := range node {
		switch {
		case nimSchemaValueKeys[key]:
			if _, isBool := val.(bool); isBool {
				delete(node, key)
				continue
			}
			sanitizeNIMValue(val, aliases)
		case nimSchemaListKeys[key]:
			if list, ok := val.([]any); ok {
				for _, item := range list {
					sanitizeNIMValue(item, aliases)
				}
			}
		case nimSchemaMapKeys[key]:
			if m, ok := val.(map[string]any); ok {
				for _, sub := range m {
					sanitizeNIMValue(sub, aliases)
				}
			}
		}
	}
	if props, ok := node["properties"].(map[string]any); ok {
		renamed := map[string]any{}
		for name, schema := range props {
			newName := name
			if name == "type" {
				newName = nimAliasPrefix + name
				aliases[newName] = name
			}
			renamed[newName] = schema
		}
		node["properties"] = renamed
		if req, ok := node["required"].([]any); ok {
			for i, r := range req {
				if s, ok := r.(string); ok && s == "type" {
					req[i] = nimAliasPrefix + s
				}
			}
		}
	}
}

func sanitizeNIMValue(val any, aliases map[string]string) {
	switch v := val.(type) {
	case bool:
		return // boolean subschema — dropped by the caller's key loop
	case map[string]any:
		sanitizeNIMSchema(v, aliases)
	case []any:
		for _, item := range v {
			sanitizeNIMValue(item, aliases)
		}
	}
}

// reverseNIMArgs maps minted `_nim_arg_*` argument names back to the originals.
func reverseNIMArgs(toolName string, args map[string]any, aliases map[string]map[string]string) map[string]any {
	am := aliases[toolName]
	if len(am) == 0 {
		return args
	}
	out := make(map[string]any, len(args))
	for k, v := range args {
		if orig, ok := am[k]; ok {
			out[orig] = v
		} else {
			out[k] = v
		}
	}
	return out
}

// -- WorkBuddy business errors ----------------------------------------------

// workbuddyBusinessError detects a business error that rides HTTP 200 (the
// upstream stream-only shape) and converts it to a WiwiError.
func workbuddyBusinessError(body []byte) *WiwiError {
	var data map[string]any
	if json.Unmarshal(body, &data) != nil {
		return nil
	}
	if _, hasChoices := data["choices"]; hasChoices {
		return nil
	}
	if _, ok := data["error"]; ok {
		return &WiwiError{Status: 502, EType: "api_error",
			Message: "workbuddy upstream error: " + ExtractErrorMessage(body), Retryable: true}
	}
	if code, ok := data["code"]; ok {
		return &WiwiError{Status: 502, EType: "api_error",
			Message:   fmt.Sprintf("workbuddy upstream code %v: %s", code, ExtractErrorMessage(body)),
			Retryable: true}
	}
	return nil
}

// -- Anthropic prompt cache + output_config ---------------------------------

// anthropicApplyPromptCache injects cache_control breakpoints on the stable
// prefix (last tool definition + last system block) when enabled.
func anthropicApplyPromptCache(body map[string]any, minTokens int) {
	if minTokens <= 0 {
		minTokens = 1024
	}
	if tools, ok := body["tools"].([]any); ok && len(tools) > 0 {
		if last, ok := tools[len(tools)-1].(map[string]any); ok {
			last["cache_control"] = map[string]any{"type": "ephemeral"}
		}
	}
	if sys, ok := body["system"].([]any); ok && len(sys) > 0 {
		if last, ok := sys[len(sys)-1].(map[string]any); ok {
			last["cache_control"] = map[string]any{"type": "ephemeral"}
		}
	}
}

// anthropicOutputConfig emits Anthropic's native output_config for effort and
// structured outputs.
func anthropicOutputConfig(g ir.GenParams) map[string]any {
	oc := map[string]any{}
	if g.Effort != nil && *g.Effort != "" {
		oc["effort"] = *g.Effort
	}
	if g.ResponseFormat != nil && g.ResponseFormat.Type == "json_schema" && g.ResponseFormat.JSONSchema != nil {
		schema := g.ResponseFormat.JSONSchema
		if inner, ok := schema["schema"].(map[string]any); ok {
			schema = inner
		}
		oc["format"] = map[string]any{"type": "json_schema", "schema": schema}
	}
	if len(oc) == 0 {
		return nil
	}
	return oc
}

// isRetryableBody reports whether an upstream 200 body should be treated as a
// retryable failure for providers that ride errors inside the stream.
func isRetryableBody(body []byte) bool {
	low := strings.ToLower(string(body))
	return strings.Contains(low, "\"error\"")
}

// -- IFM --------------------------------------------------------------------

var ifmEffortAliases = map[string]string{
	"minimal": "low", "low": "low", "medium": "medium",
	"high": "high", "xhigh": "high", "max": "high",
}

var ifmUnsupportedParams = []string{
	"prompt_cache_key", "safety_identifier", "store", "verbosity",
	"web_search_options", "prediction", "modalities", "audio", "logit_bias",
	"service_tier", "metadata", "user",
}

// applyIFM moves reasoning_effort into chat_template_kwargs, caps stop at 4, and
// drops OpenAI-platform params IFM does not document.
func applyIFM(body map[string]any, g ir.GenParams) {
	delete(body, "reasoning_effort")
	if raw := g.EffectiveReasoningEffort(); raw != "" {
		if effort, ok := ifmEffortAliases[raw]; ok {
			chat, _ := body["chat_template_kwargs"].(map[string]any)
			if chat == nil {
				chat = map[string]any{}
				body["chat_template_kwargs"] = chat
			}
			chat["reasoning_effort"] = effort
		}
	}
	if stop, ok := body["stop"].([]any); ok && len(stop) > 4 {
		body["stop"] = stop[:4]
	} else if stop, ok := body["stop"].([]string); ok && len(stop) > 4 {
		body["stop"] = stop[:4]
	}
	for _, k := range ifmUnsupportedParams {
		delete(body, k)
	}
}

// -- OpenCode ---------------------------------------------------------------

const opencodeUserAgent = "opencode/1.0.0"

// opencodeDecoyTools are the inert tools the v2 client sends; the free-tier
// request-shape gate rejects a *-free request without them.
func opencodeDecoyTools() []any {
	return []any{
		map[string]any{"type": "function", "function": map[string]any{
			"name": "read", "description": "Read a file",
			"parameters": map[string]any{"type": "object", "properties": map[string]any{"path": map[string]any{"type": "string"}}},
		}},
		map[string]any{"type": "function", "function": map[string]any{
			"name": "shell", "description": "Run a shell command",
			"parameters": map[string]any{"type": "object", "properties": map[string]any{"command": map[string]any{"type": "string"}}},
		}},
	}
}

func isOpencodeFreeModel(modelID string) bool {
	return strings.HasSuffix(modelID, "-free") ||
		strings.Contains(modelID, "-contributor-free")
}

// applyOpencodeCloak injects the decoy tools for a free model.
func applyOpencodeCloak(body map[string]any) {
	existing, _ := body["tools"].([]any)
	body["tools"] = append(existing, opencodeDecoyTools()...)
}

// stripOpencodeDecoys drops tool calls aimed at the decoy tools.
func stripOpencodeDecoys(turn *ir.AssistantTurn) {
	if len(turn.ToolCalls) == 0 {
		return
	}
	kept := turn.ToolCalls[:0]
	for _, tc := range turn.ToolCalls {
		if tc.Name == "read" || tc.Name == "shell" {
			continue
		}
		kept = append(kept, tc)
	}
	turn.ToolCalls = kept
}

// -- NIM schema memo --------------------------------------------------------

type nimCacheEntry struct {
	tools   []any
	aliases map[string]map[string]string
}

var (
	nimCacheMu    sync.Mutex
	nimCache      = map[string]nimCacheEntry{}
	nimCacheOrder []string
	nimCacheMax   = 256
)

// ConfigureToolSchemaCache bounds the NIM schema memo.
func ConfigureToolSchemaCache(max int) {
	nimCacheMu.Lock()
	defer nimCacheMu.Unlock()
	nimCacheMax = max
	for len(nimCacheOrder) > nimCacheMax {
		delete(nimCache, nimCacheOrder[0])
		nimCacheOrder = nimCacheOrder[1:]
	}
}

func nimToolsKey(tools []any) string {
	b, _ := json.Marshal(tools)
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}

func sanitizeNIMToolsCached(tools []any) ([]any, map[string]map[string]string) {
	if nimCacheMax <= 0 {
		return sanitizeNIMTools(tools)
	}
	key := nimToolsKey(tools)
	nimCacheMu.Lock()
	if e, ok := nimCache[key]; ok {
		nimCacheMu.Unlock()
		return e.tools, e.aliases
	}
	nimCacheMu.Unlock()
	clean, aliases := sanitizeNIMTools(tools)
	nimCacheMu.Lock()
	nimCache[key] = nimCacheEntry{tools: clean, aliases: aliases}
	nimCacheOrder = append(nimCacheOrder, key)
	for len(nimCacheOrder) > nimCacheMax {
		delete(nimCache, nimCacheOrder[0])
		nimCacheOrder = nimCacheOrder[1:]
	}
	nimCacheMu.Unlock()
	return clean, aliases
}
