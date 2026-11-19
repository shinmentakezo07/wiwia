package providers

import (
	"encoding/json"
	"fmt"
	"strings"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

// ResponsesAdapter speaks the OpenAI Responses wire. It is used as the
// outbound leg for OpenCode's responses-family models (gpt-*, grok-*,
// muse-spark-*) and is available as a standalone adapter.
type ResponsesAdapter struct {
	providerType string
	started      bool
	openTools    map[int]string // output_index -> call_id
}

func (a *ResponsesAdapter) ProviderType() string {
	if a.providerType == "" {
		return "openai-responses"
	}
	return a.providerType
}

func (a *ResponsesAdapter) Headers(key ProviderKeyRef) map[string]string {
	return map[string]string{
		"Authorization": "Bearer " + key.Secret,
		"Content-Type":  "application/json",
	}
}

func (a *ResponsesAdapter) BuildURL(baseURL, modelID string, stream bool) string {
	return strings.TrimRight(baseURL, "/") + "/responses"
}

func (a *ResponsesAdapter) Reset() {
	a.started = false
	a.openTools = nil
}

func (a *ResponsesAdapter) EncodeRequest(req ir.Request, modelID string, params map[string]any) (map[string]any, error) {
	g := req.GenParams
	var input []any
	for _, m := range req.Messages {
		switch m.Role {
		case ir.RoleTool:
			for _, p := range m.Parts {
				if p.Type == ir.PartToolResult {
					input = append(input, map[string]any{
						"type": "function_call_output", "call_id": p.Str("tool_use_id"),
						"output": p.Str("content"),
					})
				}
			}
		case ir.RoleAssistant:
			var content []any
			for _, p := range m.Parts {
				switch p.Type {
				case ir.PartText:
					content = append(content, map[string]any{"type": "output_text", "text": p.Str("text")})
				case ir.PartToolUse:
					args, _ := json.Marshal(p.Map("args"))
					input = append(input, map[string]any{
						"type": "function_call", "call_id": p.Str("id"),
						"name": p.Str("name"), "arguments": string(args),
					})
				}
			}
			if len(content) > 0 {
				input = append(input, map[string]any{"role": "assistant", "content": content})
			}
		default:
			var content []any
			for _, p := range m.Parts {
				switch p.Type {
				case ir.PartText:
					content = append(content, map[string]any{"type": "input_text", "text": p.Str("text")})
				case ir.PartImage:
					item := map[string]any{"type": "input_image"}
					if u := p.Str("url"); u != "" {
						item["image_url"] = u
					}
					content = append(content, item)
				}
			}
			if len(content) == 0 {
				content = append(content, map[string]any{"type": "input_text", "text": ""})
			}
			input = append(input, map[string]any{"role": string(m.Role), "content": content})
		}
	}
	body := map[string]any{
		"model":  modelID,
		"input":  input,
		"stream": req.Stream,
	}
	if g.MaxTokens != nil {
		body["max_output_tokens"] = *g.MaxTokens
	}
	if g.Temperature != nil {
		body["temperature"] = *g.Temperature
	}
	if g.TopP != nil {
		body["top_p"] = *g.TopP
	}
	if effort := g.EffectiveReasoningEffort(); effort != "" && effort != "none" {
		body["reasoning"] = map[string]any{"effort": effort}
	}
	if g.ResponseFormat != nil && g.ResponseFormat.Type == "json_schema" {
		schema := g.ResponseFormat.JSONSchema
		name := "output"
		if g.ResponseFormat.Name != nil {
			name = *g.ResponseFormat.Name
		}
		body["text"] = map[string]any{"format": map[string]any{
			"type": "json_schema", "name": name, "schema": schema,
		}}
	}
	if len(req.Tools) > 0 {
		var tools []any
		for _, t := range req.Tools {
			if t.Builtin != nil {
				continue
			}
			tool := map[string]any{
				"type": "function", "name": t.Name, "description": t.Description,
				"parameters": t.ParametersJSONSchema,
			}
			if t.Strict != nil {
				tool["strict"] = *t.Strict
			}
			tools = append(tools, tool)
		}
		if len(tools) > 0 {
			body["tools"] = tools
		}
	}
	switch tc := req.ToolChoice.(type) {
	case ir.ToolChoiceNone:
		body["tool_choice"] = "none"
	case ir.ToolChoiceRequired:
		body["tool_choice"] = "required"
	case ir.ToolChoiceNamed:
		body["tool_choice"] = map[string]any{"type": "function", "name": tc.Name}
	}
	for k, v := range params {
		if k == "max_tokens" || k == "extra_body" || k == "drop_params" || k == "opencode_free_tier_cloak" {
			continue
		}
		if _, exists := body[k]; !exists {
			body[k] = v
		}
	}
	for k, v := range req.Extras {
		if _, exists := body[k]; !exists {
			body[k] = v
		}
	}
	return body, nil
}

func (a *ResponsesAdapter) DecodeResponse(status int, body []byte) (ir.AssistantTurn, error) {
	var turn ir.AssistantTurn
	if status >= 400 {
		return turn, ErrorFromProviderStatus(status, body, a.ProviderType())
	}
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		return turn, &WiwiError{Status: 502, EType: "api_error", Retryable: true, Message: "unparseable responses body"}
	}
	for _, raw := range AsList(data["output"]) {
		item := AsDict(raw)
		switch AsString(item["type"]) {
		case "message":
			for _, c := range AsList(item["content"]) {
				part := AsDict(c)
				switch AsString(part["type"]) {
				case "output_text":
					turn.Text += AsString(part["text"])
				case "refusal":
					turn.Text += AsString(part["refusal"])
				}
			}
		case "function_call":
			args, rawArgs := argsFromAny(item["arguments"])
			turn.ToolCalls = append(turn.ToolCalls, ir.ToolUsePart{
				ID: AsString(item["call_id"]), Name: AsString(item["name"]),
				Args: args, RawArgs: rawArgs, BlockType: "tool_use",
			})
		case "reasoning":
			for _, s := range AsList(item["summary"]) {
				if txt := AsString(AsDict(s)["text"]); txt != "" {
					turn.Thinking = append(turn.Thinking, ir.ThinkingPart{Text: txt, BlockType: "thinking"})
				}
			}
		}
	}
	u := AsDict(data["usage"])
	details := AsDict(u["input_tokens_details"])
	outDetails := AsDict(u["output_tokens_details"])
	cachedInPrompt := true
	turn.Usage = ir.Usage{
		PromptTokens:     mapInt(u, "input_tokens"),
		CompletionTokens: mapInt(u, "output_tokens"),
		CachedTokens:     mapInt(details, "cached_tokens"),
		ReasoningTokens:  mapInt(outDetails, "reasoning_tokens"),
		CachedInPrompt:   &cachedInPrompt,
	}
	if len(turn.ToolCalls) > 0 {
		turn.StopReason = ir.StopToolCall
	} else {
		turn.StopReason = ir.StopStop
	}
	return turn, nil
}

func (a *ResponsesAdapter) DecodeStreamEvent(event, data string) ([]streaming.Delta, error) {
	data = strings.TrimSpace(data)
	if data == "[DONE]" {
		return []streaming.Delta{streaming.StreamEnd{}}, nil
	}
	var ev map[string]any
	if json.Unmarshal([]byte(data), &ev) != nil {
		return nil, nil
	}
	typ := AsString(ev["type"])
	if typ == "" {
		typ = event
	}
	switch typ {
	case "response.created":
		if a.started {
			return nil, nil
		}
		a.started = true
		return []streaming.Delta{streaming.StreamStart{}}, nil
	case "response.output_text.delta":
		return []streaming.Delta{streaming.TextDelta{Text: AsString(ev["delta"])}}, nil
	case "response.reasoning_summary_text.delta":
		return []streaming.Delta{streaming.ThinkingDelta{Text: AsString(ev["delta"])}}, nil
	case "response.output_item.added":
		item := AsDict(ev["item"])
		if AsString(item["type"]) == "function_call" {
			idx := intVal(ev["output_index"])
			if a.openTools == nil {
				a.openTools = map[int]string{}
			}
			callID := AsString(item["call_id"])
			a.openTools[idx] = callID
			return []streaming.Delta{streaming.ToolCallOpen{Index: idx, ID: callID, Name: AsString(item["name"])}}, nil
		}
	case "response.function_call_arguments.delta":
		return []streaming.Delta{streaming.ToolCallArgsDelta{Index: intVal(ev["output_index"]), ArgsFragment: AsString(ev["delta"])}}, nil
	case "response.output_item.done":
		item := AsDict(ev["item"])
		if AsString(item["type"]) == "function_call" {
			idx := intVal(ev["output_index"])
			return []streaming.Delta{streaming.ToolCallClose{Index: idx}}, nil
		}
	case "response.completed":
		resp := AsDict(ev["response"])
		u := AsDict(resp["usage"])
		details := AsDict(u["input_tokens_details"])
		outDetails := AsDict(u["output_tokens_details"])
		return []streaming.Delta{
			streaming.UsageFinal{
				Prompt: mapInt(u, "input_tokens"), Output: mapInt(u, "output_tokens"),
				Cached: mapInt(details, "cached_tokens"), Reasoning: mapInt(outDetails, "reasoning_tokens"),
			},
			streaming.Finish{StopReason: ir.StopStop},
			streaming.StreamEnd{},
		}, nil
	case "response.failed", "error":
		return []streaming.Delta{streaming.StreamError{Message: data, Kind: "status"}}, nil
	}
	return nil, nil
}

func AsString(v any) string {
	s, _ := v.(string)
	return s
}

func intVal(v any) int {
	switch x := v.(type) {
	case float64:
		return int(x)
	case int:
		return x
	case json.Number:
		n, _ := x.Int64()
		return int(n)
	}
	return 0
}

var _ = fmt.Sprintf
