package wire

import (
	"fmt"
	"sort"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

// messagesPassthroughKeys are the top-level Messages params the IR does not
// model but an Anthropic upstream accepts verbatim.
var messagesPassthroughKeys = map[string]struct{}{
	"service_tier": {}, "speed": {}, "metadata": {}, "fallbacks": {},
	"cache_control": {},
}

// decodeMessages decodes an Anthropic Messages request body.
func decodeMessages(body map[string]any) (ir.Request, error) {
	model := strOrEmpty(body["model"])
	if model == "" {
		return ir.Request{}, fmt.Errorf("'model' is required")
	}

	messages := make([]ir.Message, 0, 4)
	if sys, ok := body["system"].(string); ok && sys != "" {
		messages = append(messages, ir.Message{
			Role: ir.RoleSystem, Parts: []ir.Part{ir.NewTextPart(sys)},
		})
	} else if blocks, ok := body["system"].([]any); ok {
		parts := make([]ir.Part, 0, len(blocks))
		for _, raw := range blocks {
			block, ok := raw.(map[string]any)
			if !ok || strOrEmpty(block["type"]) != "text" {
				continue
			}
			part := ir.NewTextPart(strOrEmpty(block["text"]))
			if cc, ok := block["cache_control"].(map[string]any); ok {
				part.Payload["cache_control"] = cc
			}
			parts = append(parts, part)
		}
		if len(parts) > 0 {
			messages = append(messages, ir.Message{Role: ir.RoleSystem, Parts: parts})
		}
	}

	rawMessages, present := body["messages"]
	if !present {
		return ir.Request{}, fmt.Errorf("'messages' is required")
	}
	list, ok := rawMessages.([]any)
	if !ok {
		return ir.Request{}, fmt.Errorf("'messages' must be a list")
	}
	for _, raw := range list {
		m, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		role := strOrEmpty(m["role"])
		if role == "" {
			role = "user"
		}
		parts, err := decodeMessagesContent(m["content"])
		if err != nil {
			return ir.Request{}, err
		}
		if len(parts) > 0 {
			normalized := "user"
			if role == "user" || role == "assistant" || role == "system" {
				normalized = role
			}
			messages = append(messages, ir.Message{Role: ir.Role(normalized), Parts: parts})
		} else if role == "assistant" {
			// content: null is legal input (a tool-use-only turn replays it).
			messages = append(messages, ir.Message{Role: ir.RoleAssistant})
		}
	}

	tools, err := decodeMessagesTools(body)
	if err != nil {
		return ir.Request{}, err
	}

	var g ir.GenParams
	g.Temperature = floatPtr(body["temperature"])
	g.TopP = floatPtr(body["top_p"])
	g.TopK = intPtr(body["top_k"])
	if v, ok := coerceIntLoose(body["max_tokens"]); ok {
		g.MaxTokens = &v
	}
	g.Stop = stopList(body["stop_sequences"])

	toolChoice, disableParallel := decodeMessagesToolChoice(body["tool_choice"])
	g.DisableParallelToolUse = disableParallel

	if thinking, ok := body["thinking"].(map[string]any); ok {
		switch t := strOrEmpty(thinking["type"]); t {
		case "enabled":
			if rawBudget, present := thinking["budget_tokens"]; present && rawBudget != nil {
				if budget, ok := coerceIntLoose(rawBudget); ok {
					g.ThinkingBudget = &budget
					g.ThinkingType = &t
				}
				// A malformed budget drops the mode with it: the knob the
				// caller sent was the budget, and it was unusable.
			} else {
				g.ThinkingType = &t
			}
		case "adaptive", "disabled":
			g.ThinkingType = &t
			if t == "disabled" {
				none := "none"
				g.ReasoningEffort = &none
			}
		}
	}

	if oc, ok := body["output_config"].(map[string]any); ok {
		if effort := strOrEmpty(oc["effort"]); effort != "" {
			g.Effort = &effort
		}
		if format, ok := oc["format"].(map[string]any); ok && strOrEmpty(format["type"]) == "json_schema" {
			schema, ok := format["schema"].(map[string]any)
			if !ok {
				schema = map[string]any{"type": "object"}
			}
			g.ResponseFormat = &ir.ResponseFormat{
				Type: "json_schema", JSONSchema: schema,
				Name: strPtr(format["name"]), Strict: boolPtr(format["strict"]),
			}
		}
	}

	return ir.Request{
		Model:             model,
		Messages:          messages,
		Tools:             tools,
		ToolChoice:        toolChoice,
		GenParams:         g,
		Stream:            boolValue(body["stream"]),
		Extras:            pickExtras(body, messagesPassthroughKeys),
		MCPServers:        decodeMCPServers(body["mcp_servers"]),
		Container:         asObject(body["container"]),
		ContextManagement: asObject(body["context_management"]),
	}, nil
}

// decodeMCPServers parses the typed mcp_servers array; a malformed entry is
// skipped rather than failing the whole request.
func decodeMCPServers(raw any) []ir.MCPServer {
	list, ok := raw.([]any)
	if !ok {
		return nil
	}
	out := make([]ir.MCPServer, 0, len(list))
	for _, item := range list {
		obj, ok := item.(map[string]any)
		if !ok {
			continue
		}
		m := ir.MCPServer{
			Type:               strOrEmpty(obj["type"]),
			Name:               strOrEmpty(obj["name"]),
			URL:                strOrEmpty(obj["url"]),
			AuthorizationToken: strOrEmpty(obj["authorization_token"]),
		}
		if tc, ok := obj["tool_configuration"].(map[string]any); ok {
			m.ToolConfiguration = tc
		}
		out = append(out, m)
	}
	return out
}

// asObject returns raw when it is a JSON object, else nil.
func asObject(raw any) map[string]any {
	if obj, ok := raw.(map[string]any); ok {
		return obj
	}
	return nil
}

// decodeMessagesContent decodes a Messages `content` field (string or blocks).
func decodeMessagesContent(raw any) ([]ir.Part, error) {
	switch c := raw.(type) {
	case string:
		return []ir.Part{ir.NewTextPart(c)}, nil
	case []any:
		parts := make([]ir.Part, 0, len(c))
		for _, item := range c {
			block, ok := item.(map[string]any)
			if !ok {
				continue
			}
			blockType := strOrEmpty(block["type"])
			if blockType == "" {
				continue
			}
			switch {
			case blockType == "text":
				part := ir.NewTextPart(strOrEmpty(block["text"]))
				if cc := cacheControl(block); cc != nil {
					part.Payload["cache_control"] = cc
				}
				parts = append(parts, part)
			case blockType == "image":
				if part := decodeMessagesImage(block); part != nil {
					parts = append(parts, *part)
				}
			case blockType == "document":
				if part := decodeMessagesDocument(block); part != nil {
					parts = append(parts, *part)
				}
			case blockType == "tool_use":
				callID := strOrEmpty(block["id"])
				if callID == "" {
					return nil, fmt.Errorf("tool_use is missing 'id'")
				}
				args, _ := block["input"].(map[string]any)
				if args == nil {
					args = map[string]any{}
				}
				payload := map[string]any{
					"type": "tool_use", "id": callID,
					"name": strOrEmpty(block["name"]), "args": args,
					"block_type": "tool_use",
				}
				if cc := cacheControl(block); cc != nil {
					payload["cache_control"] = cc
				}
				parts = append(parts, ir.Part{Type: ir.PartToolUse, Payload: payload})
			case blockType == "server_tool_use" || blockType == "mcp_tool_use":
				name := strOrEmpty(block["name"])
				builtin := name
				if builtin == "" {
					builtin = "server_tool"
				}
				args, _ := block["input"].(map[string]any)
				if args == nil {
					args = map[string]any{}
				}
				payload := map[string]any{
					"type": "tool_use", "id": strOrEmpty(block["id"]), "name": name,
					"args": args, "builtin": builtin, "block_type": blockType,
				}
				if cc := cacheControl(block); cc != nil {
					payload["cache_control"] = cc
				}
				parts = append(parts, ir.Part{Type: ir.PartToolUse, Payload: payload})
			case blockType == "search_result" || blockType == "container_upload" || blockType == "tool_reference":
				// No first-class IR part, but the block carries content the
				// model needs: render it as text rather than dropping it.
				parts = append(parts, ir.NewTextPart(mustJSON(withoutKey(block, "type"))))
			case blockType == "tool_result" || hasSuffix(blockType, "_tool_result"):
				callID := strOrEmpty(block["tool_use_id"])
				if callID == "" {
					return nil, fmt.Errorf("tool_result is missing 'tool_use_id'")
				}
				text, images, extra := anthropicToolResultContent(block["content"])
				payload := map[string]any{
					"type": "tool_result", "tool_use_id": callID, "content": text,
					"is_error":   boolValue(block["is_error"]),
					"block_type": blockType,
				}
				if cc := cacheControl(block); cc != nil {
					payload["cache_control"] = cc
				}
				if len(images) > 0 {
					payload["images"] = images
				}
				if len(extra) > 0 {
					payload["extra_blocks"] = extra
				}
				parts = append(parts, ir.Part{Type: ir.PartToolResult, Payload: payload})
			case blockType == "thinking":
				payload := map[string]any{
					"type": "thinking", "text": strOrEmpty(block["thinking"]),
					"block_type": "thinking",
				}
				if sig := strOrEmpty(block["signature"]); sig != "" {
					payload["signature"] = sig
				}
				parts = append(parts, ir.Part{Type: ir.PartThinking, Payload: payload})
			case blockType == "redacted_thinking":
				parts = append(parts, ir.Part{Type: ir.PartThinking, Payload: map[string]any{
					"type": "thinking", "text": "", "block_type": "redacted_thinking",
					"data": strOrEmpty(block["data"]),
				}})
			}
		}
		return parts, nil
	}
	return nil, nil
}

func decodeMessagesImage(block map[string]any) *ir.Part {
	src, ok := block["source"].(map[string]any)
	if !ok {
		return nil
	}
	var part ir.Part
	switch strOrEmpty(src["type"]) {
	case "base64":
		data := strOrEmpty(src["data"])
		if data == "" {
			return nil
		}
		mime := strOrEmpty(src["media_type"])
		if mime == "" {
			mime = "image/png"
		}
		part = ir.NewImagePart("", data, mime)
	case "url":
		url := strOrEmpty(src["url"])
		if url == "" {
			return nil
		}
		part = ir.NewImagePart(url, "", "")
	case "file":
		fileID := strOrEmpty(src["file_id"])
		if fileID == "" {
			return nil
		}
		part = ir.NewImagePart("", "", "")
		part.Payload["file_id"] = fileID
	default:
		return nil
	}
	if cc := cacheControl(block); cc != nil {
		part.Payload["cache_control"] = cc
	}
	return &part
}

func decodeMessagesDocument(block map[string]any) *ir.Part {
	src, ok := block["source"].(map[string]any)
	if !ok {
		return nil
	}
	payload := map[string]any{"type": "document"}
	switch strOrEmpty(src["type"]) {
	case "base64":
		data := strOrEmpty(src["data"])
		if data == "" {
			return nil
		}
		mime := strOrEmpty(src["media_type"])
		if mime == "" {
			mime = "application/pdf"
		}
		payload["b64"] = data
		payload["mime"] = mime
	case "url":
		url := strOrEmpty(src["url"])
		if url == "" {
			return nil
		}
		payload["url"] = url
	case "file":
		fileID := strOrEmpty(src["file_id"])
		if fileID == "" {
			return nil
		}
		payload["file_id"] = fileID
	default:
		return nil
	}
	if title := strOrEmpty(block["title"]); title != "" {
		payload["name"] = title
	}
	if cc := cacheControl(block); cc != nil {
		payload["cache_control"] = cc
	}
	return &ir.Part{Type: ir.PartDocument, Payload: payload}
}

// anthropicToolResultContent flattens a tool_result content field into text
// plus any image blocks and non-text blocks worth re-emitting.
func anthropicToolResultContent(raw any) (string, []map[string]any, []map[string]any) {
	switch c := raw.(type) {
	case nil:
		return "", nil, nil
	case string:
		return c, nil, nil
	case []any:
		var texts []string
		var images, extra []map[string]any
		for _, item := range c {
			block, ok := item.(map[string]any)
			if !ok {
				continue
			}
			blockType := strOrEmpty(block["type"])
			if blockType == "text" {
				if s, ok := block["text"].(string); ok {
					texts = append(texts, s)
				}
				continue
			}
			if blockType == "image" || blockType == "input_image" {
				if part := decodeMessagesImage(block); part != nil {
					images = append(images, part.Payload)
				}
				continue
			}
			extra = append(extra, block)
		}
		text := joinNonEmpty(texts, " ")
		if text == "" && len(extra) > 0 {
			text = mustJSON(extra)
		}
		return text, images, extra
	}
	return mustJSON(raw), nil, nil
}

func decodeMessagesTools(body map[string]any) ([]ir.Tool, error) {
	rawTools, present := body["tools"]
	if !present {
		return nil, nil
	}
	list, ok := rawTools.([]any)
	if !ok {
		return nil, fmt.Errorf("'tools' must be a list")
	}
	tools := make([]ir.Tool, 0, len(list))
	for _, raw := range list {
		t, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		toolType := strOrEmpty(t["type"])
		_, hasSchema := t["input_schema"]
		if toolType == "" || toolType == "custom" || hasSchema {
			params, ok := t["input_schema"].(map[string]any)
			if !ok {
				params = map[string]any{"type": "object"}
			}
			tools = append(tools, ir.Tool{
				Name:                 strOrEmpty(t["name"]),
				Description:          strOrEmpty(t["description"]),
				ParametersJSONSchema: params,
				CacheControl:         cacheControl(t),
			})
			continue
		}
		// Provider-hosted server tool: keep it builtin-shaped so no surface
		// mangles it into a function tool.
		name := strOrEmpty(t["name"])
		if name == "" {
			name = toolType
		}
		builtin := ir.CanonicalFor("anthropic", toolType)
		if builtin == "" {
			builtin = toolType
		}
		config := map[string]any{ir.WireTypeKey: toolType, ir.WireSurfaceKey: "anthropic"}
		for _, key := range ir.BuiltinConfigKeys {
			if v, ok := t[key]; ok {
				config[key] = v
			}
		}
		tools = append(tools, ir.Tool{
			Name: name, Builtin: &builtin, BuiltinConfig: config,
			CacheControl: cacheControl(t),
		})
	}
	return tools, nil
}

func decodeMessagesToolChoice(raw any) (ir.ToolChoice, *bool) {
	tc, ok := raw.(map[string]any)
	if !ok {
		return nil, nil
	}
	disableParallel := boolPtr(tc["disable_parallel_tool_use"])
	switch strOrEmpty(tc["type"]) {
	case "any":
		return ir.ToolChoiceRequired{}, disableParallel
	case "tool":
		return ir.ToolChoiceNamed{Name: strOrEmpty(tc["name"])}, disableParallel
	case "auto":
		return ir.ToolChoiceAuto{}, disableParallel
	case "none":
		return ir.ToolChoiceNone{}, disableParallel
	}
	return nil, disableParallel
}

// --- non-streaming encode ---------------------------------------------------

func encodeMessagesResponse(turn ir.AssistantTurn, model string) map[string]any {
	content := make([]any, 0, len(turn.Thinking)+len(turn.ToolCalls)+1)
	for _, t := range turn.Thinking {
		if t.BlockType == "redacted_thinking" {
			content = append(content, map[string]any{
				"type": "redacted_thinking", "data": t.Data,
			})
			continue
		}
		block := map[string]any{"type": "thinking", "thinking": t.Text}
		if t.Signature != "" {
			block["signature"] = t.Signature
		}
		content = append(content, block)
	}
	if turn.Text != "" {
		content = append(content, map[string]any{"type": "text", "text": turn.Text})
	}
	toolBlocks := 0
	for _, tc := range turn.ToolCalls {
		if tc.Builtin != "" {
			continue // provider-hosted call: the client cannot run it
		}
		content = append(content, map[string]any{
			"type": "tool_use", "id": tc.ID, "name": tc.Name,
			"input": argsOrEmpty(tc.Args),
		})
		toolBlocks++
	}
	if len(content) == 0 {
		content = []any{map[string]any{"type": "text", "text": ""}}
	}

	stop := anthropicStop(turn.StopReason)
	if !toolFinishValid(turn.StopReason, toolBlocks) {
		stop = "end_turn"
	}

	var stopSequence any
	if turn.StopSequence != nil {
		stopSequence = *turn.StopSequence
	}

	u := turn.Usage
	return map[string]any{
		"id":            respID("msg_"),
		"type":          "message",
		"role":          "assistant",
		"model":         model,
		"content":       content,
		"stop_reason":   stop,
		"stop_sequence": stopSequence,
		"usage": anthropicUsage(
			u.PromptTokens, u.CompletionTokens, u.CachedTokens,
			u.CacheCreationTokens, u.ReasoningTokens,
			u.CachedInPrompt != nil && *u.CachedInPrompt,
		),
	}
}

// --- streaming encode -------------------------------------------------------

type messagesStreamEncoder struct {
	id    string
	model string

	blockIdx     int
	openBlock    string // "" | "text" | "thinking" | "tool"
	openTool     int
	toolBlocks   map[int]int
	lastThinkIdx int

	usage    *streaming.UsageFinal
	stop     ir.StopReason
	stopSeq  string
	sawCalls bool
	deferred deferredBuffer
}

func newMessagesStreamEncoder(model, reqID string) *messagesStreamEncoder {
	return &messagesStreamEncoder{
		id:           "msg_" + reqID,
		model:        model,
		openBlock:    "text",
		openTool:     -1,
		toolBlocks:   make(map[int]int),
		lastThinkIdx: -1,
		stop:         ir.StopStop,
	}
}

func (e *messagesStreamEncoder) Start() []string {
	return []string{
		mustJSON(map[string]any{
			"type": "message_start",
			"message": map[string]any{
				"id": e.id, "type": "message", "role": "assistant",
				"model": e.model, "content": []any{},
				"stop_reason": nil, "stop_sequence": nil,
				"usage": map[string]any{
					"input_tokens": 0, "output_tokens": 0,
					"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
				},
			},
		}),
		// The real API opens a text block right after message_start.
		e.openTextBlock(),
	}
}

// openTextBlock opens a text content block at the next index.
func (e *messagesStreamEncoder) openTextBlock() string {
	idx := e.blockIdx
	e.blockIdx++
	e.openBlock = "text"
	return mustJSON(map[string]any{
		"type": "content_block_start", "index": idx,
		"content_block": map[string]any{"type": "text", "text": ""},
	})
}

// openThinkingBlock opens a thinking content block at the next index.
func (e *messagesStreamEncoder) openThinkingBlock() string {
	idx := e.blockIdx
	e.blockIdx++
	e.openBlock = "thinking"
	e.lastThinkIdx = idx
	return mustJSON(map[string]any{
		"type": "content_block_start", "index": idx,
		"content_block": map[string]any{"type": "thinking", "thinking": ""},
	})
}

// closeOpenBlock stops whichever block is currently open.
func (e *messagesStreamEncoder) closeOpenBlock() []string {
	if e.openBlock == "" {
		return nil
	}
	idx := e.blockIdx - 1
	if e.openBlock == "tool" && e.openTool >= 0 {
		delete(e.toolBlocks, e.openTool)
	}
	e.openBlock = ""
	e.openTool = -1
	return []string{mustJSON(map[string]any{
		"type": "content_block_stop", "index": idx,
	})}
}

func (e *messagesStreamEncoder) Encode(d streaming.Delta) []string {
	switch v := d.(type) {
	case streaming.StreamStart:
		return nil // message_start was emitted by Start
	case streaming.TextDelta:
		if v.Text == "" {
			return nil
		}
		if e.openBlock == "tool" {
			e.deferred.add("text", v.Text)
			return nil
		}
		var out []string
		if e.openBlock != "text" {
			out = append(out, e.closeOpenBlock()...)
			out = append(out, e.openTextBlock())
		}
		out = append(out, mustJSON(map[string]any{
			"type": "content_block_delta", "index": e.blockIdx - 1,
			"delta": map[string]any{"type": "text_delta", "text": v.Text},
		}))
		return out
	case streaming.ThinkingDelta:
		return e.encodeThinking(v)
	case streaming.ToolCallOpen:
		out := e.closeOpenBlock()
		blockType := v.BlockType
		if blockType == "" {
			blockType = "tool_use"
		}
		if v.Builtin != "" {
			blockType = "server_tool_use"
			if v.BlockType != "" {
				blockType = v.BlockType
			}
		}
		idx := e.blockIdx
		e.blockIdx++
		e.toolBlocks[v.Index] = idx
		e.openBlock = "tool"
		e.openTool = v.Index
		e.sawCalls = true
		out = append(out, mustJSON(map[string]any{
			"type": "content_block_start", "index": idx,
			"content_block": map[string]any{
				"type": blockType, "id": v.ID, "name": v.Name, "input": map[string]any{},
			},
		}))
		return out
	case streaming.ToolCallArgsDelta:
		idx, ok := e.toolBlocks[v.Index]
		if !ok || v.ArgsFragment == "" {
			return nil
		}
		return []string{mustJSON(map[string]any{
			"type": "content_block_delta", "index": idx,
			"delta": map[string]any{"type": "input_json_delta", "partial_json": v.ArgsFragment},
		})}
	case streaming.ToolCallClose:
		idx, ok := e.toolBlocks[v.Index]
		if !ok {
			return nil
		}
		delete(e.toolBlocks, v.Index)
		if e.openTool == v.Index {
			e.openBlock = ""
			e.openTool = -1
		}
		out := []string{mustJSON(map[string]any{
			"type": "content_block_stop", "index": idx,
		})}
		if len(e.toolBlocks) == 0 {
			out = append(out, e.flushDeferred()...)
		}
		return out
	case streaming.ServerToolResultDelta:
		out := e.closeOpenBlock()
		block := make(map[string]any, len(v.Block)+1)
		for k, val := range v.Block {
			block[k] = val
		}
		if strOrEmpty(block["type"]) == "" {
			block["type"] = "server_tool_result"
		}
		idx := e.blockIdx
		e.blockIdx++
		out = append(out, mustJSON(map[string]any{
			"type": "content_block_start", "index": idx, "content_block": block,
		}))
		out = append(out, mustJSON(map[string]any{
			"type": "content_block_stop", "index": idx,
		}))
		return out
	case streaming.UsageFinal:
		u := v
		e.usage = &u
		return nil
	case streaming.Finish:
		e.stop = v.StopReason
		e.stopSeq = v.StopSequence
		return nil
	case streaming.StreamError:
		// Close whatever is open BEFORE the error frame: a stream that dies
		// mid-block would otherwise leave a content_block_start with no
		// matching stop. The error frame is terminal, so buffered prose must
		// not be flushed after it.
		out := e.closeOpenBlock()
		remaining := make([]int, 0, len(e.toolBlocks))
		for irIndex := range e.toolBlocks {
			remaining = append(remaining, irIndex)
		}
		sort.Ints(remaining)
		for _, irIndex := range remaining {
			out = append(out, mustJSON(map[string]any{
				"type": "content_block_stop", "index": e.toolBlocks[irIndex],
			}))
		}
		e.toolBlocks = make(map[int]int)
		e.deferred.drain()
		out = append(out, mustJSON(map[string]any{
			"type": "error",
			"error": map[string]any{
				"type":    anthropicErrorType(v.Status, streaming.StreamErrorType(v)),
				"message": v.ErrorText(),
			},
		}))
		return out
	}
	return nil
}

func (e *messagesStreamEncoder) encodeThinking(v streaming.ThinkingDelta) []string {
	var out []string
	if v.Text != "" {
		if e.openBlock == "tool" {
			e.deferred.add("thinking", v.Text)
			return nil
		}
		if e.openBlock != "thinking" {
			out = append(out, e.closeOpenBlock()...)
			out = append(out, e.openThinkingBlock())
		}
		out = append(out, mustJSON(map[string]any{
			"type": "content_block_delta", "index": e.blockIdx - 1,
			"delta": map[string]any{"type": "thinking_delta", "thinking": v.Text},
		}))
	}
	if v.Signature != "" && e.lastThinkIdx >= 0 {
		// A late signature rides the most recent thinking block.
		out = append(out, mustJSON(map[string]any{
			"type": "content_block_delta", "index": e.lastThinkIdx,
			"delta": map[string]any{"type": "signature_delta", "signature": v.Signature},
		}))
	}
	return out
}

func (e *messagesStreamEncoder) flushDeferred() []string {
	items := e.deferred.drain()
	if len(items) == 0 {
		return nil
	}
	out := make([]string, 0, len(items)*3)
	for _, item := range items {
		idx := e.blockIdx
		e.blockIdx++
		if item.kind == "thinking" {
			out = append(out, mustJSON(map[string]any{
				"type": "content_block_start", "index": idx,
				"content_block": map[string]any{"type": "thinking", "thinking": ""},
			}))
			out = append(out, mustJSON(map[string]any{
				"type": "content_block_delta", "index": idx,
				"delta": map[string]any{"type": "thinking_delta", "thinking": item.text},
			}))
		} else {
			out = append(out, mustJSON(map[string]any{
				"type": "content_block_start", "index": idx,
				"content_block": map[string]any{"type": "text", "text": ""},
			}))
			out = append(out, mustJSON(map[string]any{
				"type": "content_block_delta", "index": idx,
				"delta": map[string]any{"type": "text_delta", "text": item.text},
			}))
		}
		out = append(out, mustJSON(map[string]any{
			"type": "content_block_stop", "index": idx,
		}))
	}
	return out
}

func (e *messagesStreamEncoder) End() []string {
	out := e.closeOpenBlock()

	// Parallel tool calls are siblings and may close out of order; every
	// remaining open block needs its own stop.
	remaining := make([]int, 0, len(e.toolBlocks))
	for irIndex := range e.toolBlocks {
		remaining = append(remaining, irIndex)
	}
	sort.Ints(remaining)
	for _, irIndex := range remaining {
		out = append(out, mustJSON(map[string]any{
			"type": "content_block_stop", "index": e.toolBlocks[irIndex],
		}))
	}
	e.toolBlocks = make(map[int]int)

	out = append(out, e.flushDeferred()...)

	stop := anthropicStop(e.stop)
	if !toolFinishValid(e.stop, boolCount(e.sawCalls)) {
		stop = "end_turn"
	}
	var stopSequence any
	if stop == "stop_sequence" && e.stopSeq != "" {
		stopSequence = e.stopSeq
	}

	u := streaming.UsageFinal{}
	if e.usage != nil {
		u = *e.usage
	}
	out = append(out, mustJSON(map[string]any{
		"type": "message_delta",
		"delta": map[string]any{
			"stop_reason": stop, "stop_sequence": stopSequence,
		},
		"usage": anthropicUsage(
			u.Prompt, u.Output, u.Cached, u.CacheCreation, u.Reasoning,
			u.CachedInPrompt != nil && *u.CachedInPrompt,
		),
	}))
	out = append(out, mustJSON(map[string]any{"type": "message_stop"}))
	return out
}

// --- small helpers ----------------------------------------------------------

func cacheControl(block map[string]any) map[string]any {
	cc, _ := block["cache_control"].(map[string]any)
	return cc
}

func argsOrEmpty(args map[string]any) map[string]any {
	if args == nil {
		return map[string]any{}
	}
	return args
}

func withoutKey(m map[string]any, key string) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		if k == key {
			continue
		}
		out[k] = v
	}
	return out
}

func hasSuffix(s, suffix string) bool {
	return len(s) >= len(suffix) && s[len(s)-len(suffix):] == suffix
}
