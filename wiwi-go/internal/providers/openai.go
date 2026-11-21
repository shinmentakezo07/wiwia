package providers

import (
	"encoding/json"
	"fmt"
	"strings"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/oauth"
	"wiwi-go/internal/streaming"
	"wiwi-go/internal/versions"
)

// OpenAIAdapter speaks the OpenAI Chat Completions wire. Seven provider types
// share this shape (openai, openai-compatible, gmicloud, bai, openrouter,
// nvidia-nim, opencode) plus four thin variants (see variants.go); each is
// registered with its own providerType so decoding can branch where the wire
// shapes actually differ.
type OpenAIAdapter struct {
	providerType string

	// Per-stream decode state (cleared by Reset).
	started    bool
	finished   bool
	ended      bool
	openTools  map[int]*openToolState
	nimAliases map[string]map[string]string
	nimSchemas map[string]map[string]any
	nimFramer  *miniMaxFramer

	// OpenCode free-tier decoy names this request injected; calls to these
	// names are stripped on decode, while a client's own read/shell survives.
	opencodeDecoys map[string]bool
}

func (a *OpenAIAdapter) ProviderType() string { return a.providerType }

func (a *OpenAIAdapter) Headers(key ProviderKeyRef) map[string]string {
	secret := key.Secret
	for _, prefix := range []string{oauth.ClinePrefix, oauth.WorkBuddyPrefix, oauth.HermesPrefix} {
		if c, ok := oauth.Decode(prefix, secret); ok {
			secret = c.AccessToken
			break
		}
	}
	h := map[string]string{
		"Authorization": "Bearer " + secret,
		"Content-Type":  "application/json",
	}
	switch a.providerType {
	case "opencode":
		h["User-Agent"] = versions.OpenCodeUA()
	case "cline":
		h["User-Agent"] = versions.ClineUA()
		h["X-Core-Version"] = versions.ClineCoreVersion()
	case "workbuddy":
		h["User-Agent"] = versions.WorkBuddyUA()
	}
	return h
}

func (a *OpenAIAdapter) BuildURL(baseURL, modelID string, stream bool) string {
	base := strings.TrimRight(baseURL, "/")
	return base + "/chat/completions"
}

func (a *OpenAIAdapter) Reset() {
	a.started = false
	a.finished = false
	a.ended = false
	a.openTools = nil
	a.nimAliases = nil
	a.nimSchemas = nil
	a.nimFramer = nil
	a.opencodeDecoys = nil
}

// -- request encoding --------------------------------------------------------

func (a *OpenAIAdapter) EncodeRequest(req ir.Request, modelID string, params map[string]any) (map[string]any, error) {
	g := req.GenParams
	// DeepSeek's round-trip rule decides whether history reasoning is kept:
	// emit it on the turns that have it, then pad (or strip) after tools are
	// known. OpenRouter and NIM carry reasoning on a different wire shape.
	deepseek := isDeepSeekModel(modelID) && a.providerType != "openrouter" && a.providerType != "nvidia-nim"
	perMsg := emitPerMessageReasoning(a.providerType) || deepseek
	body := map[string]any{
		"model":    modelID,
		"messages": encodeOpenAIMessages(req.Messages, perMsg),
		"stream":   req.Stream,
	}

	mt := 0
	hasMT := false
	if g.MaxTokens != nil {
		mt, hasMT = *g.MaxTokens, true
	} else if v, ok := asIntValue(params["max_tokens"]); ok {
		mt, hasMT = v, true
	}
	if hasMT {
		body["max_tokens"] = mt
	}
	if g.Temperature != nil {
		body["temperature"] = *g.Temperature
	}
	if g.TopP != nil {
		body["top_p"] = *g.TopP
	}
	if len(g.Stop) > 0 {
		body["stop"] = g.Stop
	}
	if g.Seed != nil {
		body["seed"] = *g.Seed
	}
	if g.N > 1 {
		body["n"] = g.N
	}
	if g.ParallelToolCalls != nil {
		body["parallel_tool_calls"] = *g.ParallelToolCalls
	} else if g.DisableParallelToolUse != nil {
		body["parallel_tool_calls"] = !*g.DisableParallelToolUse
	}
	if effort := g.EffectiveReasoningEffort(); effort != "" && validReasoningEffort[effort] && supportsReasoningEffort(a.providerType) {
		body["reasoning_effort"] = effort
	}
	if rf := g.ResponseFormat; rf != nil && rf.Type != "" && rf.Type != "text" {
		out := map[string]any{"type": rf.Type}
		if len(rf.JSONSchema) > 0 {
			js := map[string]any{"name": "response", "schema": rf.JSONSchema}
			if rf.Name != nil && *rf.Name != "" {
				js["name"] = *rf.Name
			}
			if rf.Strict != nil {
				js["strict"] = *rf.Strict
			}
			out["json_schema"] = js
		}
		body["response_format"] = out
	}
	if tools := encodeOpenAITools(req.Tools, a.providerType); len(tools) > 0 {
		body["tools"] = tools
		if tc := encodeOpenAIToolChoice(req.ToolChoice); tc != nil {
			body["tool_choice"] = tc
		}
	}
	if req.Stream && req.StreamOptionsIncludeUsage {
		body["stream_options"] = map[string]any{"include_usage": true}
	}
	// Streaming-only OAuth upstreams: force SSE and never send stream_options
	// (both reject the field). Cline defaults to xhigh thinking when the
	// client expressed no preference; WorkBuddy defaults to max.
	if a.providerType == "cline" || a.providerType == "workbuddy" {
		body["stream"] = true
		delete(body, "stream_options")
	}
	if a.providerType == "cline" && !clientSentThinking(g) {
		body["reasoning_effort"] = "xhigh"
	}
	if a.providerType == "workbuddy" {
		if effort := g.EffectiveReasoningEffort(); effort != "" {
			body["reasoning_effort"] = effort
		} else {
			body["reasoning_effort"] = "max"
		}
	}
	// Provider-specific wire fidelity.
	switch a.providerType {
	case "nvidia-nim":
		if tools, ok := body["tools"].([]any); ok && len(tools) > 0 {
			clean, aliases := sanitizeNIMToolsCached(tools)
			body["tools"] = clean
			a.nimAliases = aliases
		}
		a.nimSchemas = map[string]map[string]any{}
		for _, t := range req.Tools {
			if t.Builtin == nil {
				a.nimSchemas[t.Name] = t.ParametersJSONSchema
			}
		}
		a.nimFramer = &miniMaxFramer{}
	case "openrouter":
		stripHistoryReasoning(body)
		applyOpenRouterReasoning(body, g)
	case "ifm":
		applyIFM(body, g)
	case "opencode":
		if isOpencodeFreeModel(modelID) && opencodeCloakRequested(params) {
			a.opencodeDecoys = applyOpencodeCloak(body)
		}
	case "workbuddy":
		normalizeWorkBuddyToolChoice(body)
		applyWorkBuddyMessages(body)
	}

	if deepseek {
		// Tools on the wire decide the rule (the OpenCode free-tier cloak adds
		// its own tools, so read the body, not the caller's tool list).
		hasTools := false
		if t, ok := body["tools"].([]any); ok && len(t) > 0 {
			hasTools = true
		}
		if hasTools && deepseekThinkingEnabled(g) {
			applyDeepSeekReasoningRoundtrip(body, g, modelID, true)
		} else {
			// No tools upstream: DeepSeek ignores history reasoning, so send
			// none (older deployments 400 on it).
			stripHistoryReasoning(body)
		}
	}

	// Deployment params: extra_body first, then any other non-control key.
	mergeDefaults(body, AsDict(params["extra_body"]))
	for k, v := range params {
		switch k {
		case "extra_body", "provider_type", "drop_params", "max_tokens",
			"prompt_cache", "prompt_cache_min_tokens", "opencode_free_tier_cloak":
			continue
		}
		setDefault(body, k, v)
	}
	// Caller extras: the Chat wire forwards the standard params it does not
	// model above; anything else rides through only when drop_params is off
	// (mirrors the Python adapter's _STANDARD policy).
	dropParams := true
	if v, ok := params["drop_params"].(bool); ok {
		dropParams = v
	}
	for k, v := range req.Extras {
		if !dropParams || openAIStandardExtras[k] {
			if _, exists := body[k]; !exists {
				body[k] = v
			}
		}
	}
	return body, nil
}

// openAIStandardExtras are Chat/Responses params the IR does not model but an
// OpenAI-shaped upstream accepts verbatim (from the Python adapter).
var openAIStandardExtras = map[string]bool{
	"frequency_penalty": true, "presence_penalty": true, "logprobs": true,
	"top_logprobs": true, "user": true, "verbosity": true,
	"web_search_options": true, "prediction": true, "store": true,
	"metadata": true, "prompt_cache_key": true, "safety_identifier": true,
	"modalities": true, "audio": true, "logit_bias": true, "service_tier": true,
}

var validReasoningEffort = map[string]bool{
	"none": true, "minimal": true, "low": true, "medium": true,
	"high": true, "xhigh": true, "max": true,
}

// encodeOpenAIMessages renders IR messages as Chat Completions wire messages.
func encodeOpenAIMessages(messages []ir.Message, perMsgReasoning bool) []any {
	out := make([]any, 0, len(messages))
	for _, m := range messages {
		switch m.Role {
		case ir.RoleSystem:
			out = append(out, map[string]any{"role": "system", "content": textPartsOf(m)})
			continue
		case ir.RoleTool:
			for _, p := range m.Parts {
				if p.Type != ir.PartToolResult {
					continue
				}
				blockType := p.Str("block_type")
				if blockType != "" && blockType != "tool_result" {
					// A provider-executed result answers no tool_calls entry;
					// keep its payload as assistant text.
					out = append(out, map[string]any{"role": "assistant", "content": p.Str("content")})
					continue
				}
				content := p.Str("content")
				if p.Bool("is_error") {
					content = "[tool error] " + content
				}
				out = append(out, map[string]any{
					"role": "tool", "tool_call_id": p.Str("tool_use_id"), "content": content,
				})
			}
			continue
		}

		// user / assistant: accumulate content (string or content-part list).
		var content any
		toParts := func() []any {
			switch c := content.(type) {
			case string:
				if c == "" {
					return nil
				}
				return []any{map[string]any{"type": "text", "text": c}}
			case []any:
				return c
			}
			return nil
		}
		appendText := func(s string) {
			switch c := content.(type) {
			case nil:
				content = s
			case string:
				content = c + s
			case []any:
				content = append(c, map[string]any{"type": "text", "text": s})
			}
		}
		appendBlock := func(b map[string]any) {
			content = append(toParts(), b)
		}

		var toolCalls []any
		var reasoning strings.Builder
		emittedToolResults := false
		for _, p := range m.Parts {
			switch p.Type {
			case ir.PartText:
				appendText(p.Str("text"))
			case ir.PartImage:
				if fileID := p.Str("file_id"); fileID != "" {
					appendBlock(map[string]any{"type": "file", "file": map[string]any{"file_id": fileID}})
					continue
				}
				appendBlock(map[string]any{"type": "image_url", "image_url": map[string]any{"url": imageURL(p)}})
			case ir.PartAudio:
				format := strings.TrimPrefix(p.Str("mime"), "audio/")
				if format == "" || format == p.Str("mime") {
					format = "wav"
				}
				appendBlock(map[string]any{"type": "input_audio", "input_audio": map[string]any{"data": p.Str("b64"), "format": format}})
			case ir.PartDocument:
				file := map[string]any{}
				if fileID := p.Str("file_id"); fileID != "" {
					file["file_id"] = fileID
				} else if url := p.Str("url"); url != "" {
					file["file_data"] = url
				} else if b64 := p.Str("b64"); b64 != "" {
					mime := p.Str("mime")
					if mime == "" {
						mime = "application/pdf"
					}
					file["file_data"] = "data:" + mime + ";base64," + b64
				}
				if len(file) > 0 {
					appendBlock(map[string]any{"type": "file", "file": file})
				}
			case ir.PartToolUse:
				if p.Str("builtin") != "" {
					continue
				}
				args := p.Map("args")
				if args == nil {
					args = map[string]any{}
				}
				raw, err := json.Marshal(args)
				if err != nil {
					raw = []byte("{}")
				}
				toolCalls = append(toolCalls, map[string]any{
					"id": p.Str("id"), "type": "function",
					"function": map[string]any{"name": p.Str("name"), "arguments": string(raw)},
				})
			case ir.PartThinking:
				if p.Str("block_type") == "redacted_thinking" {
					continue
				}
				reasoning.WriteString(p.Str("text"))
			case ir.PartToolResult:
				blockType := p.Str("block_type")
				if blockType != "" && blockType != "tool_result" {
					appendBlock(map[string]any{"type": "text", "text": p.Str("content")})
					continue
				}
				if m.Role != ir.RoleUser {
					continue
				}
				toolContent := p.Str("content")
				if p.Bool("is_error") {
					toolContent = "[tool error] " + toolContent
				}
				out = append(out, map[string]any{
					"role": "tool", "tool_call_id": p.Str("tool_use_id"), "content": toolContent,
				})
				emittedToolResults = true
			}
		}
		if emittedToolResults && content == nil && len(toolCalls) == 0 {
			continue
		}
		msg := map[string]any{"role": string(m.Role)}
		if content != nil {
			msg["content"] = content
		} else if len(toolCalls) == 0 {
			// Never send content: null without tool_calls (OpenRouter rejects it).
			msg["content"] = ""
		}
		if len(toolCalls) > 0 {
			msg["tool_calls"] = toolCalls
			if _, ok := msg["content"]; !ok {
				msg["content"] = nil
			}
		}
		if reasoning.Len() > 0 && m.Role == ir.RoleAssistant && perMsgReasoning {
			msg["reasoning_content"] = reasoning.String()
		}
		out = append(out, msg)
	}
	return out
}

func encodeOpenAITools(tools []ir.Tool, providerType string) []any {
	var out []any
	for _, t := range tools {
		if t.Builtin != nil {
			// Chat Completions has no hosted-tool representation except
			// OpenRouter's own web_search tool type.
			if providerType == "openrouter" {
				if wt, ok := ir.WireTypeFor("openrouter", *t.Builtin); ok {
					out = append(out, map[string]any{"type": wt})
				}
			}
			continue
		}
		schema := t.ParametersJSONSchema
		if schema == nil {
			schema = map[string]any{"type": "object", "properties": map[string]any{}}
		}
		fn := map[string]any{
			"name":        t.Name,
			"description": t.Description,
			"parameters":  schema,
		}
		if t.Strict != nil {
			fn["strict"] = *t.Strict
		}
		if len(t.InputExamples) > 0 {
			if raw, err := json.Marshal(t.InputExamples); err == nil {
				if t.Description != "" {
					fn["description"] = t.Description + "\n\nExample inputs:\n" + string(raw)
				} else {
					fn["description"] = "Example inputs:\n" + string(raw)
				}
			}
		}
		out = append(out, map[string]any{"type": "function", "function": fn})
	}
	return out
}

func encodeOpenAIToolChoice(tc ir.ToolChoice) any {
	switch c := tc.(type) {
	case ir.ToolChoiceNone, *ir.ToolChoiceNone:
		return "none"
	case ir.ToolChoiceAuto, *ir.ToolChoiceAuto:
		return "auto"
	case ir.ToolChoiceRequired, *ir.ToolChoiceRequired:
		return "required"
	case ir.ToolChoiceNamed:
		return map[string]any{"type": "function", "function": map[string]any{"name": c.Name}}
	case *ir.ToolChoiceNamed:
		return map[string]any{"type": "function", "function": map[string]any{"name": c.Name}}
	}
	return nil
}

// -- response decoding -------------------------------------------------------

func (a *OpenAIAdapter) DecodeResponse(status int, body []byte) (ir.AssistantTurn, error) {
	var turn ir.AssistantTurn
	if status >= 400 {
		return turn, ErrorFromProviderStatus(status, body, a.providerType)
	}
	if a.providerType == "workbuddy" {
		if we := workbuddyBusinessError(body); we != nil {
			return turn, we
		}
	}
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		return turn, &WiwiError{
			Status: 502, EType: "api_error", Retryable: true,
			Message: fmt.Sprintf("%s returned unparseable JSON: %v", a.providerType, err),
		}
	}
	choices := AsList(data["choices"])
	var choice map[string]any
	if len(choices) > 0 {
		choice = AsDict(choices[0])
	}
	message := AsDict(choice["message"])

	turn.Text = decodeOpenAIContent(message["content"])
	if turn.Text == "" {
		turn.Text = mapStr(message, "refusal")
	}
	if reasoning := mapStr(message, "reasoning_content"); reasoning != "" {
		turn.Thinking = append(turn.Thinking, ir.ThinkingPart{Text: reasoning, BlockType: "thinking"})
	} else if reasoning := mapStr(message, "reasoning"); reasoning != "" {
		turn.Thinking = append(turn.Thinking, ir.ThinkingPart{Text: reasoning, BlockType: "thinking"})
	}
	if a.providerType == "openrouter" {
		turn.Thinking = append(turn.Thinking, decodeOpenRouterReasoning(message)...)
	}
	for _, raw := range AsList(message["tool_calls"]) {
		tc := AsDict(raw)
		fn := AsDict(tc["function"])
		args, rawArgs := argsFromAny(fn["arguments"])
		name := mapStr(fn, "name")
		if a.providerType == "nvidia-nim" {
			args = reverseNIMArgs(name, args, a.nimAliases)
		}
		turn.ToolCalls = append(turn.ToolCalls, ir.ToolUsePart{
			ID: mapStr(tc, "id"), Name: name,
			Args: args, RawArgs: rawArgs, BlockType: "tool_use",
		})
	}
	turn.StopReason = normalizeFinishReason(choice["finish_reason"])
	if lp, ok := choice["logprobs"].(map[string]any); ok {
		turn.Logprobs = lp
	}

	u := AsDict(data["usage"])
	promptDetails := AsDict(u["prompt_tokens_details"])
	completionDetails := AsDict(u["completion_tokens_details"])
	cachedInPrompt := true
	turn.Usage = ir.Usage{
		PromptTokens:     mapInt(u, "prompt_tokens"),
		CompletionTokens: mapInt(u, "completion_tokens"),
		CachedTokens:     mapInt(promptDetails, "cached_tokens"),
		ReasoningTokens:  mapInt(completionDetails, "reasoning_tokens"),
		CachedInPrompt:   &cachedInPrompt,
	}
	return turn, nil
}

// decodeOpenAIContent flattens message content (string or content-part list).
func decodeOpenAIContent(v any) string {
	switch c := v.(type) {
	case string:
		return c
	case []any:
		var b strings.Builder
		for _, raw := range c {
			part := AsDict(raw)
			if part["type"] == "text" {
				b.WriteString(mapStr(part, "text"))
			}
		}
		return b.String()
	}
	return ""
}

// -- stream decoding ---------------------------------------------------------

func (a *OpenAIAdapter) DecodeStreamEvent(event, data string) ([]streaming.Delta, error) {
	var out []streaming.Delta
	trimmed := strings.TrimSpace(data)

	if trimmed == "[DONE]" {
		a.ensureStarted(&out, "")
		if a.providerType == "nvidia-nim" && a.nimFramer != nil {
			if tail := a.nimFramer.Finish(); tail != "" {
				out = append(out, streaming.TextDelta{Text: tail})
			}
		}
		closed := a.closeAllTools(&out)
		if closed && !a.finished {
			out = append(out, streaming.Finish{StopReason: ir.StopToolCall})
			a.finished = true
		}
		if !a.ended {
			out = append(out, streaming.StreamEnd{})
			a.ended = true
		}
		return out, nil
	}

	var chunk map[string]any
	if err := json.Unmarshal([]byte(data), &chunk); err != nil {
		return nil, nil
	}
	a.ensureStarted(&out, mapStr(chunk, "model"))

	// In-band error frame: some OpenAI-compatible upstreams report a failure
	// as {"error": {...}} with no choices.
	if errObj, ok := chunk["error"].(map[string]any); ok {
		a.closeAllTools(&out)
		out = append(out, streaming.StreamError{
			Message: mapStr(errObj, "message"), Kind: "status", EType: mapStr(errObj, "type"),
		})
		a.ended = true
		return out, nil
	}

	// Usage may ride in any chunk; later cumulative values replace earlier ones.
	if u, ok := chunk["usage"].(map[string]any); ok {
		promptDetails := AsDict(u["prompt_tokens_details"])
		completionDetails := AsDict(u["completion_tokens_details"])
		cachedInPrompt := true
		out = append(out, streaming.UsageFinal{
			Prompt:         mapInt(u, "prompt_tokens"),
			Cached:         mapInt(promptDetails, "cached_tokens"),
			Reasoning:      mapInt(completionDetails, "reasoning_tokens"),
			Output:         mapInt(u, "completion_tokens"),
			CachedInPrompt: &cachedInPrompt,
		})
	}

	choices := AsList(chunk["choices"])
	if len(choices) == 0 {
		return out, nil
	}
	choice := AsDict(choices[0])
	delta := AsDict(choice["delta"])

	if text := mapStr(delta, "content"); text != "" {
		if a.providerType == "nvidia-nim" {
			out = append(out, a.nimNativeDeltas(text)...)
		} else {
			out = append(out, streaming.TextDelta{Text: text})
		}
	}
	if lp, ok := choice["logprobs"].(map[string]any); ok && len(lp) > 0 {
		out = append(out, streaming.LogprobsDelta{Logprobs: lp})
	}
	if reasoning := mapStr(delta, "reasoning_content"); reasoning != "" {
		out = append(out, streaming.ThinkingDelta{Text: reasoning, BlockType: "thinking"})
	} else if reasoning := mapStr(delta, "reasoning"); reasoning != "" {
		out = append(out, streaming.ThinkingDelta{Text: reasoning, BlockType: "thinking"})
	}

	if a.openTools == nil {
		a.openTools = map[int]*openToolState{}
	}
	for i, raw := range AsList(delta["tool_calls"]) {
		tc, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		idx := i
		if n, ok := asIntValue(tc["index"]); ok {
			idx = n
		}
		id := mapStr(tc, "id")
		fn := AsDict(tc["function"])
		name := mapStr(fn, "name")
		fragment := CoerceArgsFragment(fn["arguments"])

		st, open := a.openTools[idx]
		if !open {
			if id == "" && name == "" && fragment == "" {
				continue
			}
			st = &openToolState{ID: id, Name: name}
			a.openTools[idx] = st
			out = append(out, streaming.ToolCallOpen{
				Index: idx, ID: id, Name: name, BlockType: "tool_use",
			})
		} else {
			// A repeated id is the same call echoing its header; only adopt an
			// id the first chunk omitted.
			if id != "" && st.ID == "" {
				st.ID = id
			}
			// Some upstreams fragment the name across chunks.
			if name != "" && name != st.Name && !strings.HasSuffix(st.Name, name) {
				st.Name += name
			}
		}
		if fragment != "" {
			out = append(out, streaming.ToolCallArgsDelta{Index: idx, ArgsFragment: fragment})
		}
	}

	if fr, ok := choice["finish_reason"]; ok && fr != nil {
		if s, _ := fr.(string); s != "" && !a.finished {
			a.closeAllTools(&out)
			out = append(out, streaming.Finish{StopReason: normalizeFinishReason(fr)})
			a.finished = true
		}
	}
	return out, nil
}

func (a *OpenAIAdapter) ensureStarted(out *[]streaming.Delta, model string) {
	if a.started {
		return
	}
	a.started = true
	*out = append(*out, streaming.StreamStart{Model: model})
}

// closeAllTools emits ToolCallClose for every open call, in index order, and
// reports whether anything was open.
func (a *OpenAIAdapter) closeAllTools(out *[]streaming.Delta) bool {
	if len(a.openTools) == 0 {
		return false
	}
	for _, idx := range sortedToolIndices(a.openTools) {
		*out = append(*out, streaming.ToolCallClose{Index: idx})
	}
	a.openTools = nil
	return true
}

func init() {
	for _, name := range []string{
		"openai", "openai-compatible", "gmicloud", "bai", "openrouter",
		"nvidia-nim", "opencode",
	} {
		providerType := name
		Register(name, func() Adapter { return &OpenAIAdapter{providerType: providerType} })
	}
}

// RealtimeSupported reports whether a provider type exposes the OpenAI
// Realtime WebSocket surface. Python keeps it on the OpenAI-wire adapters only:
// openai, openai-compatible, gmicloud and bai. Everything else must answer 501
// rather than relay to a made-up /realtime path.
func RealtimeSupported(providerType string) bool {
	switch providerType {
	case "openai", "openai-compatible", "gmicloud", "bai":
		return true
	}
	return false
}

// RealtimeURL derives the Realtime WebSocket endpoint from a deployment's
// base_url (https -> wss, http -> ws). ok is false for provider types with no
// realtime capability.
func RealtimeURL(providerType, baseURL string) (string, bool) {
	if !RealtimeSupported(providerType) {
		return "", false
	}
	base := strings.TrimRight(baseURL, "/")
	base = strings.Replace(base, "https://", "wss://", 1)
	base = strings.Replace(base, "http://", "ws://", 1)
	return base + "/realtime", true
}
