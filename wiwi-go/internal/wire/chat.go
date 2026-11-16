package wire

import (
	"fmt"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

var chatKnownKeys = map[string]struct{}{
	"model": {}, "messages": {}, "tools": {}, "tool_choice": {},
	"temperature": {}, "top_p": {}, "top_k": {}, "max_tokens": {},
	"max_completion_tokens": {}, "stop": {}, "seed": {}, "n": {},
	"stream": {}, "stream_options": {}, "response_format": {},
	"parallel_tool_calls": {}, "reasoning_effort": {},
}

// decodeChat decodes an OpenAI Chat Completions request body.
func decodeChat(body map[string]any) (ir.Request, error) {
	model, _ := body["model"].(string)
	if model == "" {
		return ir.Request{}, fmt.Errorf("'model' is required")
	}
	if n, ok := coerceInt(body["n"]); ok && n != 1 {
		return ir.Request{}, fmt.Errorf("'n' must be 1 (multiple choices unsupported)")
	}
	rawMessages, present := body["messages"]
	if !present {
		return ir.Request{}, fmt.Errorf("'messages' is required")
	}
	msgList, ok := rawMessages.([]any)
	if !ok {
		return ir.Request{}, fmt.Errorf("'messages' must be a list")
	}
	messages := make([]ir.Message, 0, len(msgList))
	for _, raw := range msgList {
		m, ok := raw.(map[string]any)
		if !ok {
			continue // malformed entry: skip rather than fail the request
		}
		msg, err := decodeChatMessage(m)
		if err != nil {
			return ir.Request{}, err
		}
		messages = append(messages, msg)
	}

	tools, err := decodeChatTools(body)
	if err != nil {
		return ir.Request{}, err
	}

	var g ir.GenParams
	g.Temperature = floatPtr(body["temperature"])
	g.TopP = floatPtr(body["top_p"])
	g.TopK = intPtr(body["top_k"])
	g.MaxTokens = chatMaxTokens(body)
	g.Stop = stopList(body["stop"])
	g.Seed = intPtr(body["seed"])
	g.N = 1
	if raw, ok := body["parallel_tool_calls"].(bool); ok {
		g.ParallelToolCalls = &raw
		if !raw {
			disabled := true
			g.DisableParallelToolUse = &disabled
		}
	}
	g.ReasoningEffort = strPtr(body["reasoning_effort"])
	g.ResponseFormat = decodeChatResponseFormat(body["response_format"])

	streamOpts, _ := body["stream_options"].(map[string]any)
	includeUsage := false
	if streamOpts != nil {
		includeUsage, _ = streamOpts["include_usage"].(bool)
	}

	return ir.Request{
		Model:                     model,
		Messages:                  messages,
		Tools:                     tools,
		ToolChoice:                decodeChatToolChoice(body),
		GenParams:                 g,
		Stream:                    boolValue(body["stream"]),
		StreamOptionsIncludeUsage: includeUsage,
		Extras:                    carryExtras(body, chatKnownKeys),
	}, nil
}

func decodeChatMessage(m map[string]any) (ir.Message, error) {
	role := ir.RoleUser
	if r, ok := m["role"].(string); ok {
		role = ir.Role(r)
	}
	switch role {
	case "developer":
		role = ir.RoleSystem
	case "function":
		role = ir.RoleTool
	}
	if role != ir.RoleSystem && role != ir.RoleUser && role != ir.RoleAssistant && role != ir.RoleTool {
		role = ir.RoleUser
	}

	content := m["content"]
	parts := decodeChatContent(content)

	// Reasoning models emit a separate reasoning_content field. It must be the
	// FIRST part: Anthropic's extended thinking requires the final assistant
	// turn to begin with a thinking block.
	if role == ir.RoleAssistant {
		if rc := strOrEmpty(m["reasoning_content"]); rc != "" {
			parts = append([]ir.Part{ir.NewThinkingPart(rc)}, parts...)
		}
	}

	toolCalls := chatToolCalls(m["tool_calls"])
	if role == ir.RoleAssistant && len(toolCalls) == 0 {
		// Legacy single-call protocol: function_call carries no id, so derive
		// a deterministic one that the paired role:"function" reply matches.
		if fc, ok := m["function_call"].(map[string]any); ok {
			if name := strOrEmpty(fc["name"]); name != "" {
				toolCalls = []any{map[string]any{
					"id":       legacyCallID(name),
					"function": fc,
				}}
			}
		}
	}
	for _, raw := range toolCalls {
		tc, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		fn, ok := tc["function"].(map[string]any)
		if !ok {
			continue
		}
		callID := strOrEmpty(tc["id"])
		if callID == "" {
			return ir.Message{}, fmt.Errorf("tool_call is missing 'id'")
		}
		args, rawArgs := decodeArgs(fn["arguments"])
		part := ir.NewToolUsePart(callID, strOrEmpty(fn["name"]), args)
		if rawArgs != "" {
			part.Payload["raw_args"] = rawArgs
		}
		parts = append(parts, part)
	}

	if role == ir.RoleTool {
		content, images := chatToolResultContent(content)
		callID := strOrEmpty(m["tool_call_id"])
		if callID == "" {
			if name := strOrEmpty(m["name"]); name != "" {
				callID = legacyCallID(name)
			}
		}
		if callID == "" {
			return ir.Message{}, fmt.Errorf("tool message is missing 'tool_call_id'")
		}
		payload := map[string]any{
			"type": "tool_result", "tool_use_id": callID, "content": content,
			"is_error": false, "block_type": "tool_result",
		}
		if len(images) > 0 {
			payload["images"] = images
		}
		parts = []ir.Part{{Type: ir.PartToolResult, Payload: payload}}
	}

	if len(parts) == 0 && role != ir.RoleAssistant {
		parts = []ir.Part{ir.NewTextPart("")}
	}
	return ir.Message{Role: role, Parts: parts}, nil
}

// decodeChatContent decodes a message content field (string or block array).
func decodeChatContent(content any) []ir.Part {
	switch c := content.(type) {
	case string:
		return []ir.Part{ir.NewTextPart(c)}
	case []any:
		parts := make([]ir.Part, 0, len(c))
		for _, raw := range c {
			block, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			blockType, _ := block["type"].(string)
			switch blockType {
			case "text":
				parts = append(parts, ir.NewTextPart(strOrEmpty(block["text"])))
			case "image_url":
				if img := decodeChatImage(block["image_url"]); img != nil {
					parts = append(parts, *img)
				}
			case "input_audio":
				audio, ok := block["input_audio"].(map[string]any)
				if !ok {
					continue
				}
				data := strOrEmpty(audio["data"])
				if data == "" {
					continue
				}
				format := strOrEmpty(audio["format"])
				if format == "" {
					format = "wav"
				}
				parts = append(parts, ir.Part{Type: ir.PartAudio, Payload: map[string]any{
					"type": "audio", "b64": data, "mime": "audio/" + format,
				}})
			case "file":
				file, ok := block["file"].(map[string]any)
				if !ok {
					continue
				}
				if part := decodeChatFile(file); part != nil {
					parts = append(parts, *part)
				}
			}
		}
		return parts
	}
	return nil
}

func decodeChatImage(raw any) *ir.Part {
	image, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	url := strOrEmpty(image["url"])
	if url == "" {
		return nil
	}
	if mime, b64, ok := parseDataURL(url); ok {
		if mime == "" {
			mime = "image/png"
		}
		p := ir.NewImagePart("", b64, mime)
		return &p
	}
	p := ir.NewImagePart(url, "", "")
	return &p
}

func decodeChatFile(file map[string]any) *ir.Part {
	if fileID := strOrEmpty(file["file_id"]); fileID != "" {
		return &ir.Part{Type: ir.PartDocument, Payload: map[string]any{
			"type": "document", "file_id": fileID,
		}}
	}
	raw := strOrEmpty(file["file_data"])
	if raw == "" {
		return nil
	}
	if mime, b64, ok := parseDataURL(raw); ok {
		if mime == "" {
			mime = "application/pdf"
		}
		return &ir.Part{Type: ir.PartDocument, Payload: map[string]any{
			"type": "document", "b64": b64, "mime": mime,
		}}
	}
	return &ir.Part{Type: ir.PartDocument, Payload: map[string]any{
		"type": "document", "url": raw,
	}}
}

func chatToolCalls(raw any) []any {
	list, ok := raw.([]any)
	if !ok {
		return nil
	}
	out := make([]any, 0, len(list))
	for _, tc := range list {
		if _, ok := tc.(map[string]any); ok {
			out = append(out, tc)
		}
	}
	return out
}

// chatToolResultContent coerces a tool message payload to text plus any image
// blocks the caller attached.
func chatToolResultContent(content any) (string, []map[string]any) {
	switch c := content.(type) {
	case nil:
		return "", nil
	case string:
		return c, nil
	case []any:
		texts := make([]string, 0, len(c))
		var images []map[string]any
		for _, raw := range c {
			block, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			switch block["type"] {
			case "text":
				if s, ok := block["text"].(string); ok {
					texts = append(texts, s)
				}
			case "image_url":
				if img := decodeChatImage(block["image_url"]); img != nil {
					images = append(images, img.Payload)
				}
			}
		}
		if len(texts) > 0 {
			return joinNonEmpty(texts, "\n"), images
		}
		return mustJSON(c), images
	}
	return fmt.Sprint(content), nil
}

func joinNonEmpty(items []string, sep string) string {
	out := ""
	for _, item := range items {
		if item == "" {
			continue
		}
		if out != "" {
			out += sep
		}
		out += item
	}
	return out
}

// legacyCallID derives the deterministic id the legacy `functions` protocol
// pairs an assistant call with its reply by name.
func legacyCallID(name string) string { return "call_legacy_" + name }

func decodeChatTools(body map[string]any) ([]ir.Tool, error) {
	rawTools, present := body["tools"]
	if present {
		if _, ok := rawTools.([]any); !ok {
			return nil, fmt.Errorf("'tools' must be a list")
		}
	}
	rawFunctions, present := body["functions"]
	if present {
		if _, ok := rawFunctions.([]any); !ok {
			return nil, fmt.Errorf("'functions' must be a list")
		}
	}

	var entries []map[string]any
	if list, ok := rawTools.([]any); ok {
		for _, raw := range list {
			if entry, ok := raw.(map[string]any); ok {
				entries = append(entries, entry)
			}
		}
	}
	if list, ok := rawFunctions.([]any); ok {
		for _, raw := range list {
			if fn, ok := raw.(map[string]any); ok {
				entries = append(entries, map[string]any{"type": "function", "function": fn})
			}
		}
	}

	tools := make([]ir.Tool, 0, len(entries))
	for _, entry := range entries {
		toolType, _ := entry["type"].(string)
		if toolType == "" {
			toolType = "function"
		}
		if toolType != "function" {
			continue
		}
		fn, ok := entry["function"].(map[string]any)
		if !ok {
			// Typeless entry with the function object at the top level.
			if _, named := entry["name"].(string); named {
				fn = entry
			} else {
				continue
			}
		}
		params, ok := fn["parameters"].(map[string]any)
		if !ok {
			params = map[string]any{"type": "object"}
		}
		tools = append(tools, ir.Tool{
			Name:                 strOrEmpty(fn["name"]),
			Description:          strOrEmpty(fn["description"]),
			ParametersJSONSchema: params,
			Strict:               boolPtr(fn["strict"]),
			DeferLoading:         boolPtr(fn["defer_loading"]),
		})
	}
	return tools, nil
}

func decodeChatToolChoice(body map[string]any) ir.ToolChoice {
	if tc, ok := body["tool_choice"]; ok {
		switch v := tc.(type) {
		case string:
			switch v {
			case "auto":
				return ir.ToolChoiceAuto{}
			case "none":
				return ir.ToolChoiceNone{}
			case "required":
				return ir.ToolChoiceRequired{}
			}
		case map[string]any:
			switch strOrEmpty(v["type"]) {
			case "auto":
				return ir.ToolChoiceAuto{}
			case "none":
				return ir.ToolChoiceNone{}
			case "required":
				return ir.ToolChoiceRequired{}
			case "function":
				if fn, ok := v["function"].(map[string]any); ok {
					return ir.ToolChoiceNamed{Name: strOrEmpty(fn["name"])}
				}
			}
		}
	}
	// Legacy `function_call` is the pre-tools spelling of tool_choice.
	switch v := body["function_call"].(type) {
	case string:
		switch v {
		case "auto":
			return ir.ToolChoiceAuto{}
		case "none":
			return ir.ToolChoiceNone{}
		}
	case map[string]any:
		return ir.ToolChoiceNamed{Name: strOrEmpty(v["name"])}
	}
	return nil
}

func chatMaxTokens(body map[string]any) *int {
	if v, ok := coerceInt(body["max_tokens"]); ok {
		return &v
	}
	if v, ok := coerceInt(body["max_completion_tokens"]); ok {
		return &v
	}
	return nil
}

func decodeChatResponseFormat(raw any) *ir.ResponseFormat {
	rf, ok := raw.(map[string]any)
	if !ok {
		return nil
	}
	rfType, _ := rf["type"].(string)
	if rfType != "json_object" && rfType != "json_schema" {
		return nil
	}
	format := &ir.ResponseFormat{Type: rfType}
	if rfType == "json_schema" {
		js, _ := rf["json_schema"].(map[string]any)
		schema, ok := js["schema"].(map[string]any)
		if !ok {
			schema = map[string]any{"type": "object"}
		}
		format.JSONSchema = schema
		format.Name = strPtr(js["name"])
		format.Strict = boolPtr(js["strict"])
	}
	return format
}

func boolValue(v any) bool {
	b, _ := v.(bool)
	return b
}

// --- non-streaming encode ---------------------------------------------------

func encodeChatResponse(turn ir.AssistantTurn, model string) map[string]any {
	var content any
	if turn.Text != "" {
		content = turn.Text
	}
	message := map[string]any{"role": "assistant", "content": content}

	var toolCalls []any
	for _, tc := range turn.ToolCalls {
		if tc.Builtin != "" {
			continue // provider-hosted call: the client must not run it
		}
		toolCalls = append(toolCalls, map[string]any{
			"id":   tc.ID,
			"type": "function",
			"function": map[string]any{
				"name": tc.Name, "arguments": mustJSON(tc.Args),
			},
		})
	}
	if len(toolCalls) > 0 {
		message["tool_calls"] = toolCalls
	}
	if len(turn.Thinking) > 0 {
		reasoning := ""
		for _, t := range turn.Thinking {
			reasoning += t.Text
		}
		message["reasoning_content"] = reasoning
	}

	finish := openAIFinish(turn.StopReason)
	if !toolFinishValid(turn.StopReason, len(toolCalls)) {
		finish = "stop"
	}
	choice := map[string]any{
		"index": 0, "message": message, "finish_reason": finish,
	}
	if turn.Logprobs != nil {
		choice["logprobs"] = turn.Logprobs
	}
	if turn.StopSequence != nil && *turn.StopSequence != "" {
		choice["stop_sequence"] = *turn.StopSequence
	}
	return map[string]any{
		"id":      respID("chatcmpl-"),
		"object":  "chat.completion",
		"created": unixNow(),
		"model":   model,
		"choices": []any{choice},
		"usage":   openAIUsage(turn.Usage),
	}
}

// --- streaming encode -------------------------------------------------------

type chatStreamEncoder struct {
	id      string
	created int64
	model   string

	usage    *streaming.UsageFinal
	stop     ir.StopReason
	stopSeq  string
	errored  bool
	tools    map[int]bool // indices with a client-visible Open
	open     map[int]bool // client-visible calls still open
	builtin  map[int]bool // suppressed hosted-call indices
	sawCalls bool
	deferred deferredBuffer
}

func newChatStreamEncoder(model, reqID string) *chatStreamEncoder {
	return &chatStreamEncoder{
		id:      "chatcmpl-" + reqID,
		created: unixNow(),
		model:   model,
		stop:    ir.StopStop,
		tools:   make(map[int]bool),
		open:    make(map[int]bool),
		builtin: make(map[int]bool),
	}
}

func (e *chatStreamEncoder) Start() []string {
	return []string{e.chunk(map[string]any{"role": "assistant", "content": ""}, nil, nil)}
}

// chunk builds one chunk frame. extra is merged into the choice (logprobs,
// stop_sequence).
func (e *chatStreamEncoder) chunk(delta any, finish any, extra map[string]any) string {
	choice := map[string]any{"index": 0, "delta": delta, "finish_reason": finish}
	for k, v := range extra {
		choice[k] = v
	}
	return mustJSON(map[string]any{
		"id":      e.id,
		"object":  "chat.completion.chunk",
		"created": e.created,
		"model":   e.model,
		"choices": []any{choice},
	})
}

func (e *chatStreamEncoder) Encode(d streaming.Delta) []string {
	switch v := d.(type) {
	case streaming.StreamStart:
		return nil // the opening role chunk was emitted by Start
	case streaming.TextDelta:
		if v.Text == "" {
			return nil
		}
		if len(e.open) > 0 {
			e.deferred.add("text", v.Text)
			return nil
		}
		return []string{e.chunk(map[string]any{"content": v.Text}, nil, nil)}
	case streaming.ThinkingDelta:
		if v.Text == "" {
			return nil // signature-only delta: nothing to show
		}
		if len(e.open) > 0 {
			e.deferred.add("thinking", v.Text)
			return nil
		}
		return []string{e.chunk(map[string]any{"reasoning_content": v.Text}, nil, nil)}
	case streaming.ToolCallOpen:
		if v.Builtin != "" {
			e.builtin[v.Index] = true
			return nil
		}
		e.tools[v.Index] = true
		e.open[v.Index] = true
		e.sawCalls = true
		return []string{e.chunk(map[string]any{"tool_calls": []any{map[string]any{
			"index": v.Index, "id": v.ID, "type": "function",
			"function": map[string]any{"name": v.Name, "arguments": ""},
		}}}, nil, nil)}
	case streaming.ToolCallArgsDelta:
		if e.builtin[v.Index] || !e.tools[v.Index] {
			return nil
		}
		return []string{e.chunk(map[string]any{"tool_calls": []any{map[string]any{
			"index":    v.Index,
			"function": map[string]any{"arguments": v.ArgsFragment},
		}}}, nil, nil)}
	case streaming.ToolCallClose:
		delete(e.builtin, v.Index)
		if e.open[v.Index] {
			delete(e.open, v.Index)
			if len(e.open) == 0 {
				return e.flushDeferred()
			}
		}
		return nil
	case streaming.UsageFinal:
		u := v
		e.usage = &u
		return nil
	case streaming.LogprobsDelta:
		if len(v.Logprobs) == 0 {
			return nil
		}
		return []string{e.chunk(map[string]any{}, nil, map[string]any{"logprobs": v.Logprobs})}
	case streaming.Finish:
		e.stop = v.StopReason
		e.stopSeq = v.StopSequence
		return nil
	case streaming.StreamError:
		e.errored = true
		eType := streaming.StreamErrorType(v)
		return []string{mustJSON(map[string]any{
			"error": map[string]any{
				"message": v.ErrorText(), "type": eType, "code": eType,
			},
		})}
	}
	return nil
}

func (e *chatStreamEncoder) flushDeferred() []string {
	items := e.deferred.drain()
	if len(items) == 0 {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		key := "content"
		if item.kind == "thinking" {
			key = "reasoning_content"
		}
		out = append(out, e.chunk(map[string]any{key: item.text}, nil, nil))
	}
	return out
}

func (e *chatStreamEncoder) End() []string {
	if e.errored {
		// The error frame was terminal: no finish chunk may follow it. [DONE]
		// still terminates the SSE stream for the client's parser.
		return []string{"[DONE]"}
	}
	out := e.flushDeferred()

	stop := e.stop
	if !toolFinishValid(stop, boolCount(e.sawCalls)) {
		stop = ir.StopStop
	}
	finish := openAIFinish(stop)
	var extra map[string]any
	if finish == "stop" && e.stopSeq != "" {
		extra = map[string]any{"stop_sequence": e.stopSeq}
	}
	out = append(out, e.chunk(map[string]any{}, finish, extra))

	if e.usage != nil {
		out = append(out, mustJSON(map[string]any{
			"id":      e.id,
			"object":  "chat.completion.chunk",
			"created": e.created,
			"model":   e.model,
			"choices": []any{},
			"usage":   openAIStreamUsage(*e.usage),
		}))
	}
	return append(out, "[DONE]")
}

func boolCount(b bool) int {
	if b {
		return 1
	}
	return 0
}
