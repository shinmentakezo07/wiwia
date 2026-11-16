package wire

import (
	"fmt"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

var responsesKnownKeys = map[string]struct{}{
	"model": {}, "input": {}, "instructions": {}, "previous_response_id": {},
	"tools": {}, "tool_choice": {}, "parallel_tool_calls": {}, "reasoning": {},
	"text": {}, "stream": {}, "temperature": {}, "top_p": {},
	"max_output_tokens": {}, "stop": {}, "seed": {}, "top_k": {},
}

// decodeResponses decodes an OpenAI Responses request body. History supplied
// via previous_response_id is the server's job to prepend, not the codec's.
func decodeResponses(body map[string]any) (ir.Request, error) {
	model := strOrEmpty(body["model"])
	if model == "" {
		return ir.Request{}, fmt.Errorf("'model' is required")
	}

	messages := make([]ir.Message, 0, 4)
	if instructions := strOrEmpty(body["instructions"]); instructions != "" {
		messages = append(messages, ir.Message{
			Role: ir.RoleSystem, Parts: []ir.Part{ir.NewTextPart(instructions)},
		})
	}

	rawInput := body["input"]
	var items []any
	switch input := rawInput.(type) {
	case string:
		items = []any{map[string]any{"type": "message", "role": "user", "content": input}}
	case []any:
		items = input
	case nil:
		items = nil
	default:
		return ir.Request{}, fmt.Errorf("'input' must be a string or a list")
	}

	for _, raw := range items {
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		itemType := strOrEmpty(item["type"])
		if itemType == "" {
			itemType = "message"
		}
		switch itemType {
		case "message":
			messages = append(messages, decodeResponsesMessage(item))
		case "function_call":
			callID := strOrEmpty(item["call_id"])
			if callID == "" {
				return ir.Request{}, fmt.Errorf("function_call is missing 'call_id'")
			}
			args, rawArgs := decodeArgs(item["arguments"])
			payload := map[string]any{
				"type": "tool_use", "id": callID,
				"name": strOrEmpty(item["name"]), "args": args,
				"block_type": "tool_use",
			}
			if rawArgs != "" {
				payload["raw_args"] = rawArgs
			}
			messages = append(messages, ir.Message{
				Role:  ir.RoleAssistant,
				Parts: []ir.Part{{Type: ir.PartToolUse, Payload: payload}},
			})
		case "function_call_output":
			callID := strOrEmpty(item["call_id"])
			if callID == "" {
				return ir.Request{}, fmt.Errorf("function_call_output is missing 'call_id'")
			}
			payload := map[string]any{
				"type": "tool_result", "tool_use_id": callID,
				"content": responsesItemText(item), "is_error": false,
				"block_type": "tool_result",
			}
			if images := responsesItemImages(item); len(images) > 0 {
				payload["images"] = images
			}
			messages = append(messages, ir.Message{
				Role:  ir.RoleTool,
				Parts: []ir.Part{{Type: ir.PartToolResult, Payload: payload}},
			})
		case "web_search_call", "tool_search_call":
			callID := strOrEmpty(item["id"])
			if callID == "" {
				callID = strOrEmpty(item["call_id"])
			}
			if callID == "" {
				continue // an id-less hosted call pairs with nothing
			}
			query := ""
			if action, ok := item["action"].(map[string]any); ok {
				query = strOrEmpty(action["query"])
			}
			builtin := "web_search"
			if itemType == "tool_search_call" {
				builtin = "tool_search_bm25"
			}
			messages = append(messages, ir.Message{
				Role: ir.RoleAssistant,
				Parts: []ir.Part{{Type: ir.PartToolUse, Payload: map[string]any{
					"type": "tool_use", "id": callID, "name": builtin,
					"args": map[string]any{"query": query}, "builtin": builtin,
					"block_type": "server_tool_use",
				}}},
			})
		case "reasoning":
			var texts []string
			if summary, ok := item["summary"].([]any); ok {
				for _, raw := range summary {
					if s, ok := raw.(map[string]any); ok {
						if text := strOrEmpty(s["text"]); text != "" {
							texts = append(texts, text)
						}
					}
				}
			}
			text := joinNonEmpty(texts, " ")
			blob := strOrEmpty(item["encrypted_content"])
			if text == "" && blob == "" {
				continue
			}
			payload := map[string]any{
				"type": "thinking", "text": text, "block_type": "thinking",
			}
			if blob != "" {
				payload["encrypted_content"] = blob
			}
			messages = append(messages, ir.Message{
				Role:  ir.RoleAssistant,
				Parts: []ir.Part{{Type: ir.PartThinking, Payload: payload}},
			})
		}
	}

	tools, err := decodeResponsesTools(body)
	if err != nil {
		return ir.Request{}, err
	}

	var g ir.GenParams
	g.Temperature = floatPtr(body["temperature"])
	g.TopP = floatPtr(body["top_p"])
	g.TopK = intPtr(body["top_k"])
	g.MaxTokens = intPtr(body["max_output_tokens"])
	g.Stop = stopList(body["stop"])
	g.Seed = intPtr(body["seed"])
	if raw, ok := body["parallel_tool_calls"].(bool); ok {
		g.ParallelToolCalls = &raw
		if !raw {
			disabled := true
			g.DisableParallelToolUse = &disabled
		}
	}
	if reasoning, ok := body["reasoning"].(map[string]any); ok {
		g.ReasoningEffort = strPtr(reasoning["effort"])
	}
	if text, ok := body["text"].(map[string]any); ok {
		if format, ok := text["format"].(map[string]any); ok && strOrEmpty(format["type"]) == "json_schema" {
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
		Model:      model,
		Messages:   messages,
		Tools:      tools,
		ToolChoice: decodeResponsesToolChoice(body["tool_choice"]),
		GenParams:  g,
		Stream:     boolValue(body["stream"]),
		Extras:     carryExtras(body, responsesKnownKeys),
	}, nil
}

func decodeResponsesMessage(item map[string]any) ir.Message {
	role := strOrEmpty(item["role"])
	parts := make([]ir.Part, 0, 2)
	switch content := item["content"].(type) {
	case string:
		parts = append(parts, ir.NewTextPart(content))
	case []any:
		for _, raw := range content {
			block, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			blockType := strOrEmpty(block["type"])
			if blockType == "" {
				if role == "assistant" {
					blockType = "output_text"
				} else {
					blockType = "input_text"
				}
			}
			switch blockType {
			case "input_text", "output_text", "text":
				parts = append(parts, ir.NewTextPart(strOrEmpty(block["text"])))
			case "input_image", "image":
				if fileID := strOrEmpty(block["file_id"]); fileID != "" {
					part := ir.NewImagePart("", "", "")
					part.Payload["file_id"] = fileID
					parts = append(parts, part)
					continue
				}
				if part := decodeChatImage(map[string]any{"url": block["image_url"]}); part != nil {
					parts = append(parts, *part)
				}
			case "input_file":
				if part := decodeResponsesFile(block); part != nil {
					parts = append(parts, *part)
				}
			case "input_audio":
				audio, _ := block["input_audio"].(map[string]any)
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
			}
		}
	}
	irRole := ir.RoleUser
	switch role {
	case "system", "developer":
		irRole = ir.RoleSystem
	case "assistant":
		irRole = ir.RoleAssistant
	}
	return ir.Message{Role: irRole, Parts: parts}
}

func decodeResponsesFile(block map[string]any) *ir.Part {
	if fileID := strOrEmpty(block["file_id"]); fileID != "" {
		return &ir.Part{Type: ir.PartDocument, Payload: map[string]any{
			"type": "document", "file_id": fileID,
		}}
	}
	if data := strOrEmpty(block["file_data"]); data != "" {
		if mime, b64, ok := parseDataURL(data); ok {
			if mime == "" {
				mime = "application/pdf"
			}
			return &ir.Part{Type: ir.PartDocument, Payload: map[string]any{
				"type": "document", "b64": b64, "mime": mime,
			}}
		}
		return &ir.Part{Type: ir.PartDocument, Payload: map[string]any{
			"type": "document", "url": data,
		}}
	}
	return nil
}

// responsesItemText resolves a function_call_output's payload to text.
func responsesItemText(item map[string]any) string {
	if output := strOrEmpty(item["output"]); output != "" {
		return output
	}
	switch content := item["content"].(type) {
	case string:
		return content
	case []any:
		var texts []string
		for _, raw := range content {
			if block, ok := raw.(map[string]any); ok {
				if text := strOrEmpty(block["text"]); text != "" {
					texts = append(texts, text)
				}
			}
		}
		if len(texts) > 0 {
			return joinNonEmpty(texts, "\n")
		}
	}
	return ""
}

// responsesItemImages collects image blocks attached to a tool result.
func responsesItemImages(item map[string]any) []map[string]any {
	content, ok := item["content"].([]any)
	if !ok {
		return nil
	}
	var images []map[string]any
	for _, raw := range content {
		block, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		if blockType := strOrEmpty(block["type"]); blockType != "input_image" && blockType != "image" {
			continue
		}
		if part := decodeChatImage(map[string]any{"url": block["image_url"]}); part != nil {
			images = append(images, part.Payload)
		}
	}
	return images
}

func decodeResponsesTools(body map[string]any) ([]ir.Tool, error) {
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
		if toolType == "function" || toolType == "" {
			params, ok := t["parameters"].(map[string]any)
			if !ok {
				params = map[string]any{"type": "object"}
			}
			tools = append(tools, ir.Tool{
				Name:                 strOrEmpty(t["name"]),
				Description:          strOrEmpty(t["description"]),
				ParametersJSONSchema: params,
				Strict:               boolPtr(t["strict"]),
				DeferLoading:         boolPtr(t["defer_loading"]),
			})
			continue
		}
		// Hosted builtin: keep it builtin-shaped.
		name := strOrEmpty(t["name"])
		if name == "" {
			name = toolType
		}
		builtin := ir.CanonicalFor("openai_responses", toolType)
		if builtin == "" {
			builtin = toolType
		}
		config := map[string]any{ir.WireTypeKey: toolType, ir.WireSurfaceKey: "openai_responses"}
		for _, key := range ir.BuiltinConfigKeys {
			if v, ok := t[key]; ok {
				config[key] = v
			}
		}
		tools = append(tools, ir.Tool{Name: name, Builtin: &builtin, BuiltinConfig: config})
	}
	return tools, nil
}

func decodeResponsesToolChoice(raw any) ir.ToolChoice {
	switch v := raw.(type) {
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
		case "function":
			return ir.ToolChoiceNamed{Name: strOrEmpty(v["name"])}
		case "auto":
			return ir.ToolChoiceAuto{}
		case "none":
			return ir.ToolChoiceNone{}
		case "required":
			return ir.ToolChoiceRequired{}
		}
	}
	return nil
}

// --- non-streaming encode ---------------------------------------------------

// responsesIncompleteReasons maps a stop reason to the Responses
// incomplete_details.reason vocabulary.
var responsesIncompleteReasons = map[ir.StopReason]string{
	ir.StopLength:                "max_output_tokens",
	ir.StopContentFilter:         "content_filter",
	ir.StopContextWindowExceeded: "max_output_tokens",
	ir.StopPauseTurn:             "pause_turn",
}

func encodeResponsesResponse(turn ir.AssistantTurn, model string) map[string]any {
	output := make([]any, 0, len(turn.Thinking)+len(turn.ToolCalls)+1)
	for _, t := range turn.Thinking {
		item := map[string]any{
			"type": "reasoning", "id": respID("rs_"),
			"summary": []any{map[string]any{"type": "summary_text", "text": t.Text}},
		}
		if t.EncryptedContent != "" {
			item["encrypted_content"] = t.EncryptedContent
		}
		output = append(output, item)
	}
	if turn.Text != "" || len(turn.ToolCalls) == 0 {
		output = append(output, map[string]any{
			"type": "message", "id": respID("msg_"), "status": "completed",
			"role": "assistant",
			"content": []any{map[string]any{
				"type": "output_text", "text": turn.Text, "annotations": []any{},
			}},
		})
	}
	for _, tc := range turn.ToolCalls {
		if tc.Builtin != "" {
			continue // hosted call: no client-side function to dispatch
		}
		output = append(output, map[string]any{
			"type": "function_call", "id": respID("fc_"), "call_id": tc.ID,
			"name": tc.Name, "arguments": mustJSON(tc.Args),
		})
	}

	u := turn.Usage
	prompt := u.PromptTokens
	if u.CachedInPrompt == nil || !*u.CachedInPrompt {
		prompt += u.CachedTokens
	}

	resp := map[string]any{
		"id":         respID("resp_"),
		"object":     "response",
		"created_at": unixNow(),
		"model":      model,
		"output":     output,
		"usage": responsesUsage(
			prompt, u.CompletionTokens, u.CachedTokens, u.ReasoningTokens,
		),
	}
	if reason, ok := responsesIncompleteReasons[turn.StopReason]; ok {
		resp["status"] = "incomplete"
		resp["incomplete_details"] = map[string]any{"reason": reason}
	} else {
		resp["status"] = "completed"
	}
	if turn.StopSequence != nil && *turn.StopSequence != "" {
		resp["stop_sequence"] = *turn.StopSequence
	}
	if turn.Logprobs != nil {
		resp["logprobs"] = turn.Logprobs
	}
	return resp
}

// --- streaming encode -------------------------------------------------------

type responsesToolState struct {
	itemID   string
	outIndex int
	name     string
	callID   string
	args     string
}

type responsesStreamEncoder struct {
	id    string
	model string
	seq   int

	openItem   string // "" | "message" | "thinking" | "tool"
	openItemID string
	openOut    int
	openText   string

	openTool int
	tools    map[int]*responsesToolState
	output   []any

	usage    *streaming.UsageFinal
	stop     ir.StopReason
	stopSeq  string
	errored  bool
	deferred deferredBuffer
}

func newResponsesStreamEncoder(model, reqID string) *responsesStreamEncoder {
	return &responsesStreamEncoder{
		id:       "resp_" + reqID,
		model:    model,
		openTool: -1,
		tools:    make(map[int]*responsesToolState),
		stop:     ir.StopStop,
	}
}

func (e *responsesStreamEncoder) event(eventType string, payload map[string]any) string {
	frame := make(map[string]any, len(payload)+2)
	frame["type"] = eventType
	frame["sequence_number"] = e.seq
	for k, v := range payload {
		frame[k] = v
	}
	e.seq++
	return mustJSON(frame)
}

func (e *responsesStreamEncoder) Start() []string {
	return []string{e.event("response.created", map[string]any{
		"response": map[string]any{
			"id": e.id, "object": "response", "status": "in_progress",
			"model": e.model, "output": []any{},
		},
	})}
}

// nextOutputIndex reserves the next output item slot.
func (e *responsesStreamEncoder) nextOutputIndex() int {
	idx := len(e.output)
	e.output = append(e.output, nil)
	return idx
}

func (e *responsesStreamEncoder) putOutput(idx int, item any) {
	for len(e.output) <= idx {
		e.output = append(e.output, nil)
	}
	e.output[idx] = item
}

func (e *responsesStreamEncoder) Encode(d streaming.Delta) []string {
	switch v := d.(type) {
	case streaming.StreamStart:
		return nil // response.created was emitted by Start
	case streaming.TextDelta:
		if v.Text == "" {
			return nil
		}
		if e.openItem == "tool" {
			e.deferred.add("text", v.Text)
			return nil
		}
		out := e.ensureItem("message")
		e.openText += v.Text
		out = append(out, e.event("response.output_text.delta", map[string]any{
			"item_id": e.openItemID, "output_index": e.openOut,
			"content_index": 0, "delta": v.Text,
		}))
		return out
	case streaming.ThinkingDelta:
		if v.Text == "" {
			return nil
		}
		if e.openItem == "tool" {
			e.deferred.add("thinking", v.Text)
			return nil
		}
		out := e.ensureItem("thinking")
		e.openText += v.Text
		out = append(out, e.event("response.reasoning_summary_text.delta", map[string]any{
			"item_id": e.openItemID, "output_index": e.openOut,
			"summary_index": 0, "delta": v.Text,
		}))
		return out
	case streaming.ToolCallOpen:
		out := e.closeOpenItem()
		outIndex := e.nextOutputIndex()
		state := &responsesToolState{
			itemID:   fmt.Sprintf("fc_%s_%d", e.id, outIndex),
			outIndex: outIndex, name: v.Name, callID: v.ID,
		}
		e.tools[v.Index] = state
		e.openItem = "tool"
		e.openItemID = state.itemID
		e.openOut = outIndex
		e.openTool = v.Index
		out = append(out, e.event("response.output_item.added", map[string]any{
			"output_index": outIndex,
			"item": map[string]any{
				"type": "function_call", "id": state.itemID, "call_id": v.ID,
				"name": v.Name, "arguments": "",
			},
		}))
		return out
	case streaming.ToolCallArgsDelta:
		state, ok := e.tools[v.Index]
		if !ok || v.ArgsFragment == "" {
			return nil
		}
		state.args += v.ArgsFragment
		return []string{e.event("response.function_call_arguments.delta", map[string]any{
			"item_id": state.itemID, "output_index": state.outIndex,
			"delta": v.ArgsFragment,
		})}
	case streaming.ToolCallClose:
		state, ok := e.tools[v.Index]
		if !ok {
			return nil
		}
		out := e.closeTool(v.Index, state)
		if len(e.tools) == 0 {
			out = append(out, e.flushDeferred()...)
		}
		return out
	case streaming.ServerToolResultDelta:
		out := e.closeOpenItem()
		block := make(map[string]any, len(v.Block)+2)
		for k, val := range v.Block {
			block[k] = val
		}
		if strOrEmpty(block["type"]) == "" {
			block["type"] = "server_tool_result"
		}
		if strOrEmpty(block["id"]) == "" {
			block["id"] = respID("srv_")
		}
		outIndex := e.nextOutputIndex()
		e.putOutput(outIndex, block)
		out = append(out, e.event("response.output_item.done", map[string]any{
			"output_index": outIndex, "item": block,
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
		e.errored = true
		out := e.closeOpenItem()
		remaining := make([]int, 0, len(e.tools))
		for irIndex := range e.tools {
			remaining = append(remaining, irIndex)
		}
		for _, irIndex := range remaining {
			out = append(out, e.closeTool(irIndex, e.tools[irIndex])...)
		}
		e.deferred.drain()
		out = append(out, e.event("response.failed", map[string]any{
			"response": map[string]any{
				"id": e.id, "status": "failed",
				"error": map[string]any{
					"code":    streaming.StreamErrorType(v),
					"message": v.ErrorText(),
				},
			},
		}))
		return out
	}
	return nil
}

// ensureItem opens a message/thinking output item when the current one differs.
func (e *responsesStreamEncoder) ensureItem(kind string) []string {
	if e.openItem == kind {
		return nil
	}
	out := e.closeOpenItem()
	outIndex := e.nextOutputIndex()
	e.openItem = kind
	e.openOut = outIndex
	e.openText = ""
	var item map[string]any
	if kind == "thinking" {
		e.openItemID = fmt.Sprintf("rs_%s_%d", e.id, outIndex)
		item = map[string]any{
			"type": "reasoning", "id": e.openItemID, "summary": []any{},
		}
	} else {
		e.openItemID = fmt.Sprintf("msg_%s_%d", e.id, outIndex)
		item = map[string]any{
			"type": "message", "id": e.openItemID, "status": "in_progress",
			"role": "assistant", "content": []any{},
		}
	}
	out = append(out, e.event("response.output_item.added", map[string]any{
		"output_index": outIndex, "item": item,
	}))
	if kind == "message" {
		out = append(out, e.event("response.content_part.added", map[string]any{
			"item_id": e.openItemID, "output_index": outIndex, "content_index": 0,
			"part": map[string]any{"type": "output_text", "text": "", "annotations": []any{}},
		}))
	}
	return out
}

// closeOpenItem closes the message/thinking item currently open.
func (e *responsesStreamEncoder) closeOpenItem() []string {
	switch e.openItem {
	case "tool":
		if e.openTool >= 0 {
			if state, ok := e.tools[e.openTool]; ok {
				return e.closeTool(e.openTool, state)
			}
		}
		e.openItem, e.openItemID = "", ""
		e.openTool = -1
		return nil
	case "message":
		idx, itemID, text := e.openOut, e.openItemID, e.openText
		e.openItem, e.openItemID, e.openText = "", "", ""
		item := map[string]any{
			"type": "message", "id": itemID, "status": "completed",
			"role": "assistant",
			"content": []any{map[string]any{
				"type": "output_text", "text": text, "annotations": []any{},
			}},
		}
		e.putOutput(idx, item)
		return []string{
			e.event("response.output_text.done", map[string]any{
				"item_id": itemID, "output_index": idx,
				"content_index": 0, "text": text,
			}),
			e.event("response.content_part.done", map[string]any{
				"item_id": itemID, "output_index": idx, "content_index": 0,
				"part": map[string]any{
					"type": "output_text", "text": text, "annotations": []any{},
				},
			}),
			e.event("response.output_item.done", map[string]any{
				"output_index": idx, "item": item,
			}),
		}
	case "thinking":
		idx, itemID, text := e.openOut, e.openItemID, e.openText
		e.openItem, e.openItemID, e.openText = "", "", ""
		item := map[string]any{
			"type": "reasoning", "id": itemID,
			"summary": []any{map[string]any{"type": "summary_text", "text": text}},
		}
		e.putOutput(idx, item)
		return []string{
			e.event("response.reasoning_summary_text.done", map[string]any{
				"item_id": itemID, "output_index": idx,
				"summary_index": 0, "text": text,
			}),
			e.event("response.output_item.done", map[string]any{
				"output_index": idx, "item": item,
			}),
		}
	}
	return nil
}

// closeTool finishes one function_call item.
func (e *responsesStreamEncoder) closeTool(irIndex int, state *responsesToolState) []string {
	delete(e.tools, irIndex)
	if e.openTool == irIndex {
		e.openItem, e.openItemID = "", ""
		e.openTool = -1
	}
	item := map[string]any{
		"type": "function_call", "id": state.itemID, "call_id": state.callID,
		"name": state.name, "arguments": state.args, "status": "completed",
	}
	e.putOutput(state.outIndex, item)
	return []string{
		e.event("response.function_call_arguments.done", map[string]any{
			"item_id": state.itemID, "output_index": state.outIndex,
			"arguments": state.args,
		}),
		e.event("response.output_item.done", map[string]any{
			"output_index": state.outIndex, "item": item,
		}),
	}
}

func (e *responsesStreamEncoder) flushDeferred() []string {
	items := e.deferred.drain()
	if len(items) == 0 {
		return nil
	}
	var out []string
	for _, item := range items {
		kind := "message"
		if item.kind == "thinking" {
			kind = "thinking"
		}
		out = append(out, e.ensureItem(kind)...)
		e.openText += item.text
		if kind == "thinking" {
			out = append(out, e.event("response.reasoning_summary_text.delta", map[string]any{
				"item_id": e.openItemID, "output_index": e.openOut,
				"summary_index": 0, "delta": item.text,
			}))
		} else {
			out = append(out, e.event("response.output_text.delta", map[string]any{
				"item_id": e.openItemID, "output_index": e.openOut,
				"content_index": 0, "delta": item.text,
			}))
		}
		out = append(out, e.closeOpenItem()...)
	}
	return out
}

func (e *responsesStreamEncoder) End() []string {
	if e.errored {
		// response.failed was the terminal event; nothing content-bearing may
		// follow it.
		return nil
	}
	out := e.closeOpenItem()
	remaining := make([]int, 0, len(e.tools))
	for irIndex := range e.tools {
		remaining = append(remaining, irIndex)
	}
	for _, irIndex := range remaining {
		out = append(out, e.closeTool(irIndex, e.tools[irIndex])...)
	}
	out = append(out, e.flushDeferred()...)

	u := streaming.UsageFinal{}
	if e.usage != nil {
		u = *e.usage
	}
	prompt := u.Prompt
	if u.CachedInPrompt == nil || !*u.CachedInPrompt {
		prompt += u.Cached
	}

	output := make([]any, 0, len(e.output))
	for _, item := range e.output {
		if item != nil {
			output = append(output, item)
		}
	}
	resp := map[string]any{
		"id": e.id, "object": "response", "model": e.model,
		"output": output,
		"usage":  responsesUsage(prompt, u.Output, u.Cached, u.Reasoning),
	}
	eventType := "response.completed"
	if reason, ok := responsesIncompleteReasons[e.stop]; ok {
		resp["status"] = "incomplete"
		resp["incomplete_details"] = map[string]any{"reason": reason}
		eventType = "response.incomplete"
	} else {
		resp["status"] = "completed"
	}
	if e.stopSeq != "" {
		resp["stop_sequence"] = e.stopSeq
	}
	return append(out, e.event(eventType, map[string]any{"response": resp}))
}
