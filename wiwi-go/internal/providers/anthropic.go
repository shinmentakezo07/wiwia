package providers

import (
	"encoding/json"
	"fmt"
	"strings"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

const anthropicVersion = "2023-06-01"
const anthropicDefaultMaxTokens = 4096
const anthropicMinThinkingBudget = 1024

// AnthropicAdapter speaks the Messages API (content blocks, SSE event types).
type AnthropicAdapter struct {
	providerType string

	// Per-stream decode state (cleared by Reset).
	started            bool
	finished           bool
	ended              bool
	openTools          map[int]*openToolState
	pendingPrompt      int
	pendingCached      int
	pendingCacheCreate int
}

func (a *AnthropicAdapter) ProviderType() string { return a.providerType }

func (a *AnthropicAdapter) Headers(key ProviderKeyRef) map[string]string {
	return map[string]string{
		"x-api-key":         key.Secret,
		"anthropic-version": anthropicVersion,
		"Content-Type":      "application/json",
	}
}

func (a *AnthropicAdapter) BuildURL(baseURL, modelID string, stream bool) string {
	base := strings.TrimRight(baseURL, "/")
	if base == "" {
		base = "https://api.anthropic.com"
	}
	return base + "/v1/messages"
}

func (a *AnthropicAdapter) Reset() {
	a.started = false
	a.finished = false
	a.ended = false
	a.openTools = nil
	a.pendingPrompt = 0
	a.pendingCached = 0
	a.pendingCacheCreate = 0
}

// -- request encoding --------------------------------------------------------

func (a *AnthropicAdapter) EncodeRequest(req ir.Request, modelID string, params map[string]any) (map[string]any, error) {
	g := req.GenParams
	maxTokens := anthropicDefaultMaxTokens
	if g.MaxTokens != nil {
		maxTokens = *g.MaxTokens
	} else if v, ok := asIntValue(params["max_tokens"]); ok {
		maxTokens = v
	}
	body := map[string]any{
		"model":      modelID,
		"max_tokens": maxTokens,
		"messages":   encodeAnthropicMessages(req.Messages),
	}
	if system := anthropicSystem(req.Messages); system != nil {
		body["system"] = system
	}
	if g.Temperature != nil {
		body["temperature"] = *g.Temperature
	}
	if g.TopP != nil {
		body["top_p"] = *g.TopP
	}
	if g.TopK != nil {
		body["top_k"] = *g.TopK
	}
	if len(g.Stop) > 0 {
		body["stop_sequences"] = g.Stop
	}
	// Thinking configuration. The API requires budget_tokens >= 1024 and
	// max_tokens > budget_tokens, so clamp the budget and lift a positive cap.
	switch {
	case g.ThinkingType != nil && *g.ThinkingType == "adaptive":
		body["thinking"] = map[string]any{"type": "adaptive"}
	case g.ThinkingType != nil && *g.ThinkingType == "disabled":
		// thinking off: omit the key
	default:
		if budget, ok := g.EffectiveThinkingBudget(); ok && budget > 0 {
			if budget < anthropicMinThinkingBudget {
				budget = anthropicMinThinkingBudget
			}
			if maxTokens > 0 && maxTokens <= budget {
				maxTokens = budget + anthropicMinThinkingBudget
				body["max_tokens"] = maxTokens
			}
			body["thinking"] = map[string]any{"type": "enabled", "budget_tokens": budget}
		}
	}

	if tools := encodeAnthropicTools(req.Tools); len(tools) > 0 {
		body["tools"] = tools
		if tc := encodeAnthropicToolChoice(req.ToolChoice, g.DisableParallelToolUse); tc != nil {
			body["tool_choice"] = tc
		}
	} else if g.DisableParallelToolUse != nil {
		body["tool_choice"] = map[string]any{
			"type": "auto", "disable_parallel_tool_use": *g.DisableParallelToolUse,
		}
	}

	if oc := anthropicOutputConfig(g); oc != nil {
		body["output_config"] = oc
	}
	if pc, _ := params["prompt_cache"].(bool); pc {
		min := 0
		if v, ok := params["prompt_cache_min_tokens"].(int); ok {
			min = v
		}
		anthropicApplyPromptCache(body, min)
	}
	if req.Stream {
		body["stream"] = true
	}
	mergeDefaults(body, req.Extras)
	return body, nil
}

type anthropicSystemPart struct {
	text string
	cc   map[string]any
}

// anthropicSystem hoists LEADING system messages into the top-level system
// field. A mid-conversation system entry stays in messages (its position
// matters to the cache prefix).
func anthropicSystem(messages []ir.Message) any {
	var parts []anthropicSystemPart
	for _, m := range messages {
		if m.Role != ir.RoleSystem {
			break
		}
		for _, p := range m.Parts {
			if p.Type != ir.PartText {
				continue
			}
			text := p.Str("text")
			if text == "" {
				continue
			}
			cc, _ := p.Payload["cache_control"].(map[string]any)
			parts = append(parts, anthropicSystemPart{text: text, cc: cc})
		}
	}
	if len(parts) == 0 {
		return nil
	}
	hasCache := false
	for _, p := range parts {
		if len(p.cc) > 0 {
			hasCache = true
			break
		}
	}
	if !hasCache {
		texts := make([]string, 0, len(parts))
		for _, p := range parts {
			texts = append(texts, p.text)
		}
		return strings.Join(texts, "\n")
	}
	blocks := make([]any, 0, len(parts))
	for _, p := range parts {
		b := map[string]any{"type": "text", "text": p.text}
		if len(p.cc) > 0 {
			b["cache_control"] = p.cc
		}
		blocks = append(blocks, b)
	}
	return blocks
}

func encodeAnthropicMessages(messages []ir.Message) []any {
	out := make([]any, 0, len(messages))
	seenNonSystem := false
	for _, m := range messages {
		if m.Role == ir.RoleSystem && !seenNonSystem {
			continue
		}
		seenNonSystem = true

		var role string
		switch m.Role {
		case ir.RoleAssistant:
			role = "assistant"
		case ir.RoleSystem:
			role = "system"
		default:
			role = "user"
		}

		blocks := make([]any, 0, len(m.Parts))
		for _, p := range m.Parts {
			switch p.Type {
			case ir.PartText:
				text := p.Str("text")
				if text == "" {
					// Anthropic 400s on empty text blocks.
					continue
				}
				b := map[string]any{"type": "text", "text": text}
				if cc, ok := p.Payload["cache_control"].(map[string]any); ok && len(cc) > 0 {
					b["cache_control"] = cc
				}
				blocks = append(blocks, b)
			case ir.PartImage:
				blocks = append(blocks, anthropicImageBlock(p))
			case ir.PartDocument:
				doc := map[string]any{"type": "document"}
				switch {
				case p.Str("file_id") != "":
					doc["source"] = map[string]any{"type": "file", "file_id": p.Str("file_id")}
				case p.Str("url") != "":
					doc["source"] = map[string]any{"type": "url", "url": p.Str("url")}
				default:
					mime := p.Str("mime")
					if mime == "" {
						mime = "application/pdf"
					}
					doc["source"] = map[string]any{"type": "base64", "media_type": mime, "data": p.Str("b64")}
				}
				if title := p.Str("name"); title != "" {
					doc["title"] = title
				}
				if ctx := p.Str("context"); ctx != "" {
					doc["context"] = ctx
				}
				if cc, ok := p.Payload["cache_control"].(map[string]any); ok && len(cc) > 0 {
					doc["cache_control"] = cc
				}
				blocks = append(blocks, doc)
			case ir.PartAudio:
				// The Messages API has no audio content block.
				continue
			case ir.PartToolUse:
				if p.Str("builtin") != "" {
					// A provider-hosted call is only legal paired with its
					// result in the same history; unpaired replays are rejected.
					continue
				}
				blockType := p.Str("block_type")
				if blockType == "" {
					blockType = "tool_use"
				}
				args := p.Map("args")
				if args == nil {
					args = map[string]any{}
				}
				ub := map[string]any{
					"type": blockType, "id": p.Str("id"), "name": p.Str("name"), "input": args,
				}
				if cc, ok := p.Payload["cache_control"].(map[string]any); ok && len(cc) > 0 {
					ub["cache_control"] = cc
				}
				blocks = append(blocks, ub)
			case ir.PartToolResult:
				blockType := p.Str("block_type")
				if blockType == "" {
					blockType = "tool_result"
				}
				tr := map[string]any{
					"type": blockType, "tool_use_id": p.Str("tool_use_id"),
					"content": p.Str("content"),
				}
				if p.Bool("is_error") {
					tr["is_error"] = true
				}
				if cc, ok := p.Payload["cache_control"].(map[string]any); ok && len(cc) > 0 {
					tr["cache_control"] = cc
				}
				blocks = append(blocks, tr)
			case ir.PartThinking:
				if p.Str("block_type") == "redacted_thinking" {
					if data := p.Str("data"); data != "" {
						blocks = append(blocks, map[string]any{"type": "redacted_thinking", "data": data})
					}
					continue
				}
				signature := p.Str("signature")
				if signature == "" {
					// An unsigned thinking block is rejected on replay.
					continue
				}
				blocks = append(blocks, map[string]any{
					"type": "thinking", "thinking": p.Str("text"), "signature": signature,
				})
			}
		}

		if len(blocks) == 0 {
			if role != "assistant" {
				continue
			}
			// Preserve an empty assistant turn's position with the conventional
			// single-space placeholder (Anthropic rejects a truly empty block).
			blocks = []any{map[string]any{"type": "text", "text": " "}}
		}
		if len(out) > 0 {
			last := AsDict(out[len(out)-1])
			if mapStr(last, "role") == role {
				last["content"] = append(AsList(last["content"]), blocks...)
				continue
			}
		}
		out = append(out, map[string]any{"role": role, "content": blocks})
	}
	return out
}

func anthropicImageBlock(p ir.Part) map[string]any {
	var source map[string]any
	switch {
	case p.Str("file_id") != "":
		source = map[string]any{"type": "file", "file_id": p.Str("file_id")}
	case p.Str("url") != "":
		source = map[string]any{"type": "url", "url": p.Str("url")}
	default:
		mime := p.Str("mime")
		if mime == "" {
			mime = "image/png"
		}
		source = map[string]any{"type": "base64", "media_type": mime, "data": p.Str("b64")}
	}
	block := map[string]any{"type": "image", "source": source}
	if cc, ok := p.Payload["cache_control"].(map[string]any); ok && len(cc) > 0 {
		block["cache_control"] = cc
	}
	return block
}

func encodeAnthropicTools(tools []ir.Tool) []any {
	var out []any
	for _, t := range tools {
		if t.Builtin != nil {
			canonical := *t.Builtin
			if wt, ok := ir.WireTypeFor("anthropic", canonical); ok {
				entry := map[string]any{"type": wt, "name": canonical}
				for k, v := range ir.BuiltinConfigSubset(t.BuiltinConfig) {
					entry[k] = v
				}
				out = append(out, entry)
				continue
			}
			if raw := ir.PassthroughWireType("anthropic", t.BuiltinConfig); raw != "" {
				entry := map[string]any{"type": raw, "name": t.Name}
				for k, v := range ir.BuiltinConfigSubset(t.BuiltinConfig) {
					entry[k] = v
				}
				out = append(out, entry)
			}
			continue
		}
		schema := t.ParametersJSONSchema
		if schema == nil {
			schema = map[string]any{"type": "object", "properties": map[string]any{}}
		}
		entry := map[string]any{
			"name": t.Name, "description": t.Description, "input_schema": schema,
		}
		if t.Strict != nil {
			entry["strict"] = *t.Strict
		}
		if len(t.InputExamples) > 0 {
			entry["input_examples"] = t.InputExamples
		}
		if t.DeferLoading != nil {
			entry["defer_loading"] = *t.DeferLoading
		}
		if len(t.CacheControl) > 0 {
			entry["cache_control"] = t.CacheControl
		}
		out = append(out, entry)
	}
	return out
}

func encodeAnthropicToolChoice(tc ir.ToolChoice, disableParallel *bool) map[string]any {
	var obj map[string]any
	switch c := tc.(type) {
	case ir.ToolChoiceNone, *ir.ToolChoiceNone:
		obj = map[string]any{"type": "none"}
	case ir.ToolChoiceAuto, *ir.ToolChoiceAuto:
		obj = map[string]any{"type": "auto"}
	case ir.ToolChoiceRequired, *ir.ToolChoiceRequired:
		obj = map[string]any{"type": "any"}
	case ir.ToolChoiceNamed:
		obj = map[string]any{"type": "tool", "name": c.Name}
	case *ir.ToolChoiceNamed:
		obj = map[string]any{"type": "tool", "name": c.Name}
	}
	if obj != nil && disableParallel != nil {
		obj["disable_parallel_tool_use"] = *disableParallel
	}
	return obj
}

// -- response decoding -------------------------------------------------------

func (a *AnthropicAdapter) DecodeResponse(status int, body []byte) (ir.AssistantTurn, error) {
	var turn ir.AssistantTurn
	if status >= 400 {
		return turn, ErrorFromProviderStatus(status, body, a.providerType)
	}
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		return turn, &WiwiError{
			Status: 502, EType: "api_error", Retryable: true,
			Message: fmt.Sprintf("%s returned unparseable JSON: %v", a.providerType, err),
		}
	}
	for _, raw := range AsList(data["content"]) {
		block, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		btype := mapStr(block, "type")
		switch {
		case btype == "text":
			turn.Text += mapStr(block, "text")
		case btype == "thinking":
			turn.Thinking = append(turn.Thinking, ir.ThinkingPart{
				Text: mapStr(block, "thinking"), Signature: mapStr(block, "signature"),
				BlockType: "thinking",
			})
		case btype == "redacted_thinking":
			turn.Thinking = append(turn.Thinking, ir.ThinkingPart{
				BlockType: "redacted_thinking", Data: mapStr(block, "data"),
			})
		case btype == "tool_use":
			turn.ToolCalls = append(turn.ToolCalls, ir.ToolUsePart{
				ID: mapStr(block, "id"), Name: mapStr(block, "name"),
				Args: AsDict(block["input"]), BlockType: "tool_use",
			})
		case btype == "server_tool_use" || btype == "mcp_tool_use":
			builtin := mapStr(block, "name")
			if builtin == "" {
				builtin = "server_tool"
			}
			turn.ToolCalls = append(turn.ToolCalls, ir.ToolUsePart{
				ID: mapStr(block, "id"), Name: mapStr(block, "name"),
				Args: AsDict(block["input"]), Builtin: builtin, BlockType: btype,
			})
		case strings.HasSuffix(btype, "_tool_result"):
			turn.ServerBlocks = append(turn.ServerBlocks, block)
		}
	}
	turn.StopReason = anthropicStopReason(data["stop_reason"])
	if seq := mapStr(data, "stop_sequence"); seq != "" {
		turn.StopSequence = &seq
	}
	u := AsDict(data["usage"])
	outDetails := AsDict(u["output_tokens_details"])
	cachedInPrompt := false
	turn.Usage = ir.Usage{
		PromptTokens:        mapInt(u, "input_tokens"),
		CompletionTokens:    mapInt(u, "output_tokens"),
		CachedTokens:        mapInt(u, "cache_read_input_tokens"),
		CacheCreationTokens: mapInt(u, "cache_creation_input_tokens"),
		ReasoningTokens:     mapInt(outDetails, "thinking_tokens"),
		CachedInPrompt:      &cachedInPrompt,
	}
	return turn, nil
}

// anthropicBlockIndex reads a content-block index. A float that is numerically
// whole is normalized; anything else names no recoverable block.
func anthropicBlockIndex(payload map[string]any) (int, bool) {
	v, ok := payload["index"]
	if !ok {
		return 0, true
	}
	return asIntValue(v)
}

// -- stream decoding ---------------------------------------------------------

func (a *AnthropicAdapter) DecodeStreamEvent(event, data string) ([]streaming.Delta, error) {
	var out []streaming.Delta
	if strings.TrimSpace(data) == "[DONE]" {
		a.ensureStarted(&out, "")
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

	var payload map[string]any
	if err := json.Unmarshal([]byte(data), &payload); err != nil {
		return nil, nil
	}
	etype := mapStr(payload, "type")
	if etype == "" {
		etype = event
	}
	if etype != "message_start" {
		a.ensureStarted(&out, "")
	}

	switch etype {
	case "message_start":
		message := AsDict(payload["message"])
		u := AsDict(message["usage"])
		a.pendingPrompt = mapInt(u, "input_tokens")
		a.pendingCached = mapInt(u, "cache_read_input_tokens")
		a.pendingCacheCreate = mapInt(u, "cache_creation_input_tokens")
		a.started = true
		out = append(out, streaming.StreamStart{
			Model: mapStr(message, "model"), Prompt: a.pendingPrompt,
			Cached: a.pendingCached, CacheCreation: a.pendingCacheCreate,
		})

	case "content_block_start":
		cb := AsDict(payload["content_block"])
		cbType := mapStr(cb, "type")
		idx, hasIdx := anthropicBlockIndex(payload)
		switch cbType {
		case "redacted_thinking":
			out = append(out, streaming.ThinkingDelta{
				BlockType: "redacted_thinking", Data: mapStr(cb, "data"),
			})
		case "tool_use", "server_tool_use", "mcp_tool_use":
			if !hasIdx {
				return out, nil
			}
			if a.openTools == nil {
				a.openTools = map[int]*openToolState{}
			}
			if _, open := a.openTools[idx]; open {
				// A second start on a live index supersedes the first call.
				out = append(out, streaming.ToolCallClose{Index: idx})
			}
			name := mapStr(cb, "name")
			a.openTools[idx] = &openToolState{ID: mapStr(cb, "id"), Name: name}
			builtin := ""
			if cbType != "tool_use" {
				builtin = name
				if builtin == "" {
					builtin = "server_tool"
				}
			}
			out = append(out, streaming.ToolCallOpen{
				Index: idx, ID: mapStr(cb, "id"), Name: name,
				Builtin: builtin, BlockType: cbType,
			})
		}

	case "content_block_delta":
		d := AsDict(payload["delta"])
		switch mapStr(d, "type") {
		case "text_delta":
			out = append(out, streaming.TextDelta{Text: mapStr(d, "text")})
		case "thinking_delta":
			out = append(out, streaming.ThinkingDelta{Text: mapStr(d, "thinking"), BlockType: "thinking"})
		case "signature_delta":
			out = append(out, streaming.ThinkingDelta{Signature: mapStr(d, "signature")})
		case "input_json_delta":
			idx, hasIdx := anthropicBlockIndex(payload)
			if !hasIdx {
				return out, nil
			}
			if _, open := a.openTools[idx]; open {
				if frag := mapStr(d, "partial_json"); frag != "" {
					out = append(out, streaming.ToolCallArgsDelta{Index: idx, ArgsFragment: frag})
				}
			}
		}

	case "content_block_stop":
		idx, hasIdx := anthropicBlockIndex(payload)
		if !hasIdx {
			// Ambiguous: only close when exactly one call is open.
			if len(a.openTools) == 1 {
				for openIdx := range a.openTools {
					idx, hasIdx = openIdx, true
				}
			}
		}
		if hasIdx {
			if _, open := a.openTools[idx]; open {
				delete(a.openTools, idx)
				out = append(out, streaming.ToolCallClose{Index: idx})
			}
		}

	case "message_delta":
		d := AsDict(payload["delta"])
		u := AsDict(payload["usage"])
		outDetails := AsDict(u["output_tokens_details"])
		cachedInPrompt := false
		out = append(out, streaming.UsageFinal{
			Prompt: a.pendingPrompt, Cached: a.pendingCached,
			CacheCreation:  a.pendingCacheCreate,
			Reasoning:      mapInt(outDetails, "thinking_tokens"),
			Output:         mapInt(u, "output_tokens"),
			CachedInPrompt: &cachedInPrompt,
		})
		if !a.finished {
			out = append(out, streaming.Finish{
				StopReason:   anthropicStopReason(d["stop_reason"]),
				StopSequence: mapStr(d, "stop_sequence"),
			})
			a.finished = true
		}

	case "message_stop":
		if !a.ended {
			out = append(out, streaming.StreamEnd{})
			a.ended = true
		}

	case "error":
		errObj := AsDict(payload["error"])
		a.closeAllTools(&out)
		out = append(out, streaming.StreamError{
			Message: mapStr(errObj, "message"), Kind: "status", EType: mapStr(errObj, "type"),
		})
		a.ended = true

	case "ping":
		// keep-alive: no deltas
	}
	return out, nil
}

func (a *AnthropicAdapter) ensureStarted(out *[]streaming.Delta, model string) {
	if a.started {
		return
	}
	a.started = true
	*out = append(*out, streaming.StreamStart{Model: model})
}

func (a *AnthropicAdapter) closeAllTools(out *[]streaming.Delta) bool {
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
	Register("anthropic", func() Adapter { return &AnthropicAdapter{providerType: "anthropic"} })
}
