package wire

import (
	"encoding/json"
	"strings"
	"testing"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

func mustBody(t *testing.T, raw string) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatalf("invalid test JSON: %v", err)
	}
	return m
}

func decodeOK(t *testing.T, s Surface, raw string) ir.Request {
	t.Helper()
	req, err := DecodeRequest(s, mustBody(t, raw))
	if err != nil {
		t.Fatalf("DecodeRequest(%s) error: %v", s, err)
	}
	return req
}

func decodeErr(t *testing.T, s Surface, raw string) error {
	t.Helper()
	_, err := DecodeRequest(s, mustBody(t, raw))
	if err == nil {
		t.Fatalf("DecodeRequest(%s) succeeded, want error", s)
	}
	return err
}

func frame(t *testing.T, payload string) map[string]any {
	t.Helper()
	var m map[string]any
	if err := json.Unmarshal([]byte(payload), &m); err != nil {
		t.Fatalf("payload is not JSON: %v (%q)", err, payload)
	}
	return m
}

// --- chat decode ------------------------------------------------------------

func TestDecodeChatText(t *testing.T) {
	req := decodeOK(t, SurfaceChat, `{
		"model": "gpt-4o",
		"messages": [
			{"role": "system", "content": "be nice"},
			{"role": "developer", "content": "newer instructions"},
			{"role": "user", "content": "hi"}
		],
		"temperature": 0.5,
		"top_p": 0.9,
		"max_tokens": 100,
		"stop": "END",
		"seed": 7,
		"stream": true,
		"stream_options": {"include_usage": true},
		"reasoning_effort": "high",
		"custom_key": "carried"
	}`)

	if req.Model != "gpt-4o" {
		t.Fatalf("model = %q", req.Model)
	}
	if len(req.Messages) != 3 {
		t.Fatalf("messages = %d, want 3", len(req.Messages))
	}
	if req.Messages[0].Role != ir.RoleSystem || req.Messages[1].Role != ir.RoleSystem {
		t.Fatalf("developer should fold into system: %q %q",
			req.Messages[0].Role, req.Messages[1].Role)
	}
	if req.Messages[2].Role != ir.RoleUser || req.Messages[2].Parts[0].Str("text") != "hi" {
		t.Fatalf("user message decoded wrong: %+v", req.Messages[2])
	}
	g := req.GenParams
	if g.Temperature == nil || *g.Temperature != 0.5 {
		t.Fatalf("temperature = %v", g.Temperature)
	}
	if g.TopP == nil || *g.TopP != 0.9 {
		t.Fatalf("top_p = %v", g.TopP)
	}
	if g.MaxTokens == nil || *g.MaxTokens != 100 {
		t.Fatalf("max_tokens = %v", g.MaxTokens)
	}
	if len(g.Stop) != 1 || g.Stop[0] != "END" {
		t.Fatalf("stop = %v", g.Stop)
	}
	if g.Seed == nil || *g.Seed != 7 {
		t.Fatalf("seed = %v", g.Seed)
	}
	if g.ReasoningEffort == nil || *g.ReasoningEffort != "high" {
		t.Fatalf("reasoning_effort = %v", g.ReasoningEffort)
	}
	if !req.Stream || !req.StreamOptionsIncludeUsage {
		t.Fatalf("stream flags = %v/%v", req.Stream, req.StreamOptionsIncludeUsage)
	}
	if req.Extras["custom_key"] != "carried" {
		t.Fatalf("extras = %v", req.Extras)
	}
	if _, leaked := req.Extras["messages"]; leaked {
		t.Fatalf("known key leaked into extras: %v", req.Extras)
	}
}

func TestDecodeChatToolCalls(t *testing.T) {
	req := decodeOK(t, SurfaceChat, `{
		"model": "gpt-4o",
		"messages": [
			{"role": "user", "content": "weather?"},
			{"role": "assistant", "content": null, "tool_calls": [
				{"id": "call_1", "type": "function",
				 "function": {"name": "get_weather", "arguments": "{\"city\":\"Paris\"}"}}
			]},
			{"role": "tool", "tool_call_id": "call_1", "content": "sunny"}
		],
		"tools": [{"type": "function", "function": {
			"name": "get_weather", "description": "weather",
			"parameters": {"type": "object", "properties": {"city": {"type": "string"}}}
		}}],
		"tool_choice": "required"
	}`)

	if len(req.Messages) != 3 {
		t.Fatalf("messages = %d", len(req.Messages))
	}
	assistant := req.Messages[1]
	if len(assistant.Parts) != 1 || assistant.Parts[0].Type != ir.PartToolUse {
		t.Fatalf("assistant parts = %+v", assistant.Parts)
	}
	call := assistant.Parts[0]
	if call.Str("id") != "call_1" || call.Str("name") != "get_weather" {
		t.Fatalf("tool call = %+v", call.Payload)
	}
	args := call.Map("args")
	if args["city"] != "Paris" {
		t.Fatalf("tool args = %v", args)
	}

	toolMsg := req.Messages[2]
	if toolMsg.Role != ir.RoleTool || toolMsg.Parts[0].Type != ir.PartToolResult {
		t.Fatalf("tool message = %+v", toolMsg)
	}
	if toolMsg.Parts[0].Str("tool_use_id") != "call_1" ||
		toolMsg.Parts[0].Str("content") != "sunny" {
		t.Fatalf("tool result = %+v", toolMsg.Parts[0].Payload)
	}

	if len(req.Tools) != 1 || req.Tools[0].Name != "get_weather" {
		t.Fatalf("tools = %+v", req.Tools)
	}
	if _, ok := req.ToolChoice.(ir.ToolChoiceRequired); !ok {
		t.Fatalf("tool_choice = %T", req.ToolChoice)
	}
}

func TestDecodeChatErrors(t *testing.T) {
	decodeErr(t, SurfaceChat, `{"messages": [{"role":"user","content":"x"}]}`)
	decodeErr(t, SurfaceChat, `{"model": "gpt-4o"}`)
	decodeErr(t, SurfaceChat, `{"model": "gpt-4o", "messages": "nope"}`)
	decodeErr(t, SurfaceChat, `{"model": "gpt-4o", "messages": [
		{"role":"assistant","tool_calls":[{"type":"function","function":{"name":"f"}}]}]}`)
	decodeErr(t, SurfaceChat, `{"model": "gpt-4o", "messages": [
		{"role":"tool","content":"x"}]}`)
}

// --- messages decode / encode ----------------------------------------------

func TestDecodeMessages(t *testing.T) {
	req := decodeOK(t, SurfaceMessages, `{
		"model": "claude-sonnet-4",
		"system": "be terse",
		"messages": [
			{"role": "user", "content": [
				{"type": "text", "text": "hi"},
				{"type": "image", "source": {"type": "base64",
					"media_type": "image/png", "data": "AAA"}}
			]},
			{"role": "assistant", "content": [
				{"type": "tool_use", "id": "tu_1", "name": "f", "input": {"a": 1}}
			]},
			{"role": "user", "content": [
				{"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"}
			]}
		],
		"max_tokens": 256,
		"temperature": 0.2,
		"top_p": 0.9,
		"top_k": 5,
		"stop_sequences": ["x"],
		"thinking": {"type": "enabled", "budget_tokens": 1024},
		"tools": [{"name": "f", "description": "d",
			"input_schema": {"type": "object"}}],
		"tool_choice": {"type": "auto"}
	}`)

	if len(req.Messages) != 4 {
		t.Fatalf("messages = %d, want 4", len(req.Messages))
	}
	if req.Messages[0].Role != ir.RoleSystem ||
		req.Messages[0].Parts[0].Str("text") != "be terse" {
		t.Fatalf("system message = %+v", req.Messages[0])
	}
	user := req.Messages[1]
	if len(user.Parts) != 2 || user.Parts[1].Type != ir.PartImage {
		t.Fatalf("user parts = %+v", user.Parts)
	}
	if user.Parts[1].Str("b64") != "AAA" || user.Parts[1].Str("mime") != "image/png" {
		t.Fatalf("image part = %+v", user.Parts[1].Payload)
	}
	toolUse := req.Messages[2].Parts[0]
	if toolUse.Type != ir.PartToolUse || toolUse.Str("id") != "tu_1" {
		t.Fatalf("tool_use = %+v", toolUse)
	}
	if toolUse.Map("args")["a"] != float64(1) {
		t.Fatalf("tool_use args = %v", toolUse.Map("args"))
	}
	toolResult := req.Messages[3].Parts[0]
	if toolResult.Type != ir.PartToolResult ||
		toolResult.Str("tool_use_id") != "tu_1" || toolResult.Str("content") != "ok" {
		t.Fatalf("tool_result = %+v", toolResult.Payload)
	}

	g := req.GenParams
	if g.MaxTokens == nil || *g.MaxTokens != 256 {
		t.Fatalf("max_tokens = %v", g.MaxTokens)
	}
	if g.TopK == nil || *g.TopK != 5 {
		t.Fatalf("top_k = %v", g.TopK)
	}
	if len(g.Stop) != 1 || g.Stop[0] != "x" {
		t.Fatalf("stop_sequences = %v", g.Stop)
	}
	if g.ThinkingType == nil || *g.ThinkingType != "enabled" ||
		g.ThinkingBudget == nil || *g.ThinkingBudget != 1024 {
		t.Fatalf("thinking = %v/%v", g.ThinkingType, g.ThinkingBudget)
	}
	if len(req.Tools) != 1 || req.Tools[0].Name != "f" {
		t.Fatalf("tools = %+v", req.Tools)
	}
	if _, ok := req.ToolChoice.(ir.ToolChoiceAuto); !ok {
		t.Fatalf("tool_choice = %T", req.ToolChoice)
	}
}

func TestEncodeMessagesResponse(t *testing.T) {
	stopSeq := "STOP"
	turn := ir.AssistantTurn{
		Text:     "hello",
		Thinking: []ir.ThinkingPart{{Text: "hmm", Signature: "sig", BlockType: "thinking"}},
		ToolCalls: []ir.ToolUsePart{{
			ID: "tu_1", Name: "f", Args: map[string]any{"a": 1}, BlockType: "tool_use",
		}},
		StopReason:   ir.StopToolCall,
		StopSequence: &stopSeq,
		Usage: ir.Usage{
			PromptTokens: 10, CompletionTokens: 5,
			CachedTokens: 4, ReasoningTokens: 2, CacheCreationTokens: 1,
			CachedInPrompt: boolPtr(false),
		},
	}
	body := EncodeResponse(SurfaceMessages, turn, "claude-sonnet-4", ir.Request{})

	id, _ := body["id"].(string)
	if !strings.HasPrefix(id, "msg_") {
		t.Fatalf("id = %q", id)
	}
	if body["type"] != "message" || body["role"] != "assistant" {
		t.Fatalf("envelope = %v", body)
	}
	if body["stop_reason"] != "tool_use" {
		t.Fatalf("stop_reason = %v", body["stop_reason"])
	}
	if body["stop_sequence"] != "STOP" {
		t.Fatalf("stop_sequence = %v", body["stop_sequence"])
	}
	content, ok := body["content"].([]any)
	if !ok || len(content) != 3 {
		t.Fatalf("content = %v", body["content"])
	}
	if content[0].(map[string]any)["type"] != "thinking" {
		t.Fatalf("first block = %v", content[0])
	}
	if content[1].(map[string]any)["type"] != "text" {
		t.Fatalf("second block = %v", content[1])
	}
	toolBlock := content[2].(map[string]any)
	if toolBlock["type"] != "tool_use" || toolBlock["id"] != "tu_1" {
		t.Fatalf("tool block = %v", toolBlock)
	}
	usage := body["usage"].(map[string]any)
	if usage["input_tokens"] != 10 || usage["output_tokens"] != 5 {
		t.Fatalf("usage = %v", usage)
	}
	if usage["cache_read_input_tokens"] != 4 {
		t.Fatalf("cache usage = %v", usage)
	}
}

// --- responses decode -------------------------------------------------------

func TestDecodeResponses(t *testing.T) {
	req := decodeOK(t, SurfaceResponses, `{
		"model": "gpt-5",
		"instructions": "be brief",
		"input": [
			{"type": "message", "role": "user", "content": [
				{"type": "input_text", "text": "weather?"}
			]},
			{"type": "function_call", "call_id": "call_1",
			 "name": "get_weather", "arguments": "{\"city\":\"Paris\"}"},
			{"type": "function_call_output", "call_id": "call_1", "output": "sunny"}
		],
		"max_output_tokens": 64,
		"parallel_tool_calls": false,
		"reasoning": {"effort": "medium"},
		"text": {"format": {"type": "json_schema", "name": "out",
			"schema": {"type": "object"}}},
		"tools": [{"type": "function", "name": "get_weather",
			"parameters": {"type": "object"}}],
		"tool_choice": "auto"
	}`)

	if len(req.Messages) != 4 {
		t.Fatalf("messages = %d, want 4", len(req.Messages))
	}
	if req.Messages[0].Role != ir.RoleSystem {
		t.Fatalf("instructions = %+v", req.Messages[0])
	}
	if req.Messages[1].Parts[0].Str("text") != "weather?" {
		t.Fatalf("user message = %+v", req.Messages[1])
	}
	if req.Messages[2].Parts[0].Type != ir.PartToolUse ||
		req.Messages[2].Parts[0].Str("id") != "call_1" {
		t.Fatalf("function_call = %+v", req.Messages[2])
	}
	if req.Messages[3].Role != ir.RoleTool ||
		req.Messages[3].Parts[0].Str("content") != "sunny" {
		t.Fatalf("function_call_output = %+v", req.Messages[3])
	}
	if req.GenParams.MaxTokens == nil || *req.GenParams.MaxTokens != 64 {
		t.Fatalf("max_output_tokens = %v", req.GenParams.MaxTokens)
	}
	if req.GenParams.ParallelToolCalls == nil || *req.GenParams.ParallelToolCalls {
		t.Fatalf("parallel_tool_calls = %v", req.GenParams.ParallelToolCalls)
	}
	if req.GenParams.ReasoningEffort == nil || *req.GenParams.ReasoningEffort != "medium" {
		t.Fatalf("reasoning effort = %v", req.GenParams.ReasoningEffort)
	}
	if req.GenParams.ResponseFormat == nil || req.GenParams.ResponseFormat.Type != "json_schema" {
		t.Fatalf("response_format = %+v", req.GenParams.ResponseFormat)
	}
	if len(req.Tools) != 1 || req.Tools[0].Name != "get_weather" {
		t.Fatalf("tools = %+v", req.Tools)
	}

	// A plain string input is one user turn.
	simple := decodeOK(t, SurfaceResponses, `{"model":"gpt-5","input":"hello"}`)
	if len(simple.Messages) != 1 || simple.Messages[0].Parts[0].Str("text") != "hello" {
		t.Fatalf("string input = %+v", simple.Messages)
	}
}

func TestEncodeResponsesResponse(t *testing.T) {
	turn := ir.AssistantTurn{
		Text: "hello",
		ToolCalls: []ir.ToolUsePart{{
			ID: "call_1", Name: "f", Args: map[string]any{"a": 1},
		}},
		StopReason: ir.StopStop,
		Usage:      ir.Usage{PromptTokens: 3, CompletionTokens: 2},
	}
	body := EncodeResponse(SurfaceResponses, turn, "gpt-5", ir.Request{})
	if body["object"] != "response" || body["status"] != "completed" {
		t.Fatalf("envelope = %v", body)
	}
	output := body["output"].([]any)
	if len(output) != 2 {
		t.Fatalf("output = %v", output)
	}
	message := output[0].(map[string]any)
	content := message["content"].([]any)[0].(map[string]any)
	if content["type"] != "output_text" || content["text"] != "hello" {
		t.Fatalf("message item = %v", message)
	}
	call := output[1].(map[string]any)
	if call["type"] != "function_call" || call["call_id"] != "call_1" {
		t.Fatalf("function_call item = %v", call)
	}
}

// --- completions decode -----------------------------------------------------

func TestDecodeCompletions(t *testing.T) {
	req := decodeOK(t, SurfaceCompletions, `{
		"model": "gpt-3.5-turbo-instruct",
		"prompt": "Once upon a time",
		"max_tokens": 20,
		"temperature": 0.7,
		"stream": true
	}`)
	if len(req.Messages) != 1 || req.Messages[0].Role != ir.RoleUser {
		t.Fatalf("messages = %+v", req.Messages)
	}
	if req.Messages[0].Parts[0].Str("text") != "Once upon a time" {
		t.Fatalf("prompt text = %+v", req.Messages[0].Parts[0])
	}
	if req.GenParams.MaxTokens == nil || *req.GenParams.MaxTokens != 20 {
		t.Fatalf("max_tokens = %v", req.GenParams.MaxTokens)
	}
	if !req.Stream {
		t.Fatal("stream flag lost")
	}

	decodeErr(t, SurfaceCompletions, `{"model":"m"}`)
	decodeErr(t, SurfaceCompletions, `{"model":"m","prompt":[1,2,3]}`)
	decodeErr(t, SurfaceCompletions, `{"model":"m","prompt":["a","b"]}`)
	decodeErr(t, SurfaceCompletions, `{"model":"m","prompt":"x","echo":true}`)
}

func TestEncodeCompletionsResponse(t *testing.T) {
	turn := ir.AssistantTurn{
		Text: "done", StopReason: ir.StopLength,
		Usage: ir.Usage{PromptTokens: 4, CompletionTokens: 1},
	}
	body := EncodeResponse(SurfaceCompletions, turn, "m", ir.Request{})
	if body["object"] != "text_completion" {
		t.Fatalf("object = %v", body["object"])
	}
	choice := body["choices"].([]any)[0].(map[string]any)
	if choice["text"] != "done" || choice["finish_reason"] != "length" {
		t.Fatalf("choice = %v", choice)
	}
}

// --- streaming encoders -----------------------------------------------------

func TestChatStreamEncoderTextFinishDone(t *testing.T) {
	enc := NewStreamEncoder(SurfaceChat, "gpt-4o", "req1")
	start := enc.Start()
	if len(start) != 1 {
		t.Fatalf("Start frames = %d", len(start))
	}
	first := frame(t, start[0])
	if first["object"] != "chat.completion.chunk" {
		t.Fatalf("object = %v", first["object"])
	}
	delta := first["choices"].([]any)[0].(map[string]any)["delta"].(map[string]any)
	if delta["role"] != "assistant" {
		t.Fatalf("start delta = %v", delta)
	}

	if frames := enc.Encode(streaming.StreamStart{Model: "gpt-4o"}); len(frames) != 0 {
		t.Fatalf("StreamStart should be a no-op after Start: %v", frames)
	}
	for _, text := range []string{"Hel", "lo"} {
		frames := enc.Encode(streaming.TextDelta{Text: text})
		if len(frames) != 1 {
			t.Fatalf("TextDelta frames = %v", frames)
		}
		got := frame(t, frames[0])
		choice := got["choices"].([]any)[0].(map[string]any)
		if choice["delta"].(map[string]any)["content"] != text {
			t.Fatalf("content = %v", choice["delta"])
		}
	}
	if frames := enc.Encode(streaming.UsageFinal{Prompt: 10, Output: 2, Cached: 4}); len(frames) != 0 {
		t.Fatalf("UsageFinal should buffer: %v", frames)
	}
	if frames := enc.Encode(streaming.Finish{StopReason: ir.StopStop}); len(frames) != 0 {
		t.Fatalf("Finish should buffer: %v", frames)
	}

	end := enc.End()
	if len(end) != 3 {
		t.Fatalf("End frames = %d (%v)", len(end), end)
	}
	finish := frame(t, end[0])
	choice := finish["choices"].([]any)[0].(map[string]any)
	if choice["finish_reason"] != "stop" {
		t.Fatalf("finish_reason = %v", choice["finish_reason"])
	}
	usageChunk := frame(t, end[1])
	if len(usageChunk["choices"].([]any)) != 0 {
		t.Fatalf("usage chunk choices = %v", usageChunk["choices"])
	}
	usage := usageChunk["usage"].(map[string]any)
	if usage["prompt_tokens"] != float64(14) || usage["completion_tokens"] != float64(2) {
		t.Fatalf("usage = %v", usage)
	}
	if end[2] != "[DONE]" {
		t.Fatalf("terminal = %q", end[2])
	}
}

func TestChatStreamEncoderToolCalls(t *testing.T) {
	enc := NewStreamEncoder(SurfaceChat, "gpt-4o", "req2")
	enc.Start()
	open := enc.Encode(streaming.ToolCallOpen{Index: 0, ID: "call_1", Name: "f"})
	if len(open) != 1 {
		t.Fatalf("open frames = %v", open)
	}
	tc := frame(t, open[0])["choices"].([]any)[0].(map[string]any)["delta"].(map[string]any)["tool_calls"].([]any)[0].(map[string]any)
	if tc["id"] != "call_1" {
		t.Fatalf("tool call = %v", tc)
	}
	args := enc.Encode(streaming.ArgsDelta{Index: 0, ArgsFragment: `{"a":1}`})
	if len(args) != 1 || !strings.Contains(args[0], `{\"a\":1}`) {
		t.Fatalf("args frames = %v", args)
	}
	if frames := enc.Encode(streaming.ToolCallClose{Index: 0}); len(frames) != 0 {
		t.Fatalf("close frames = %v", frames)
	}
	enc.Encode(streaming.Finish{StopReason: ir.StopToolCall})
	end := enc.End()
	finish := frame(t, end[0])["choices"].([]any)[0].(map[string]any)
	if finish["finish_reason"] != "tool_calls" {
		t.Fatalf("finish_reason = %v", finish["finish_reason"])
	}
}

func TestMessagesStreamEncoder(t *testing.T) {
	enc := NewStreamEncoder(SurfaceMessages, "claude-sonnet-4", "req3")
	start := enc.Start()
	if len(start) != 2 {
		t.Fatalf("Start frames = %d", len(start))
	}
	if frame(t, start[0])["type"] != "message_start" {
		t.Fatalf("first event = %v", frame(t, start[0]))
	}
	if frame(t, start[1])["type"] != "content_block_start" {
		t.Fatalf("second event = %v", frame(t, start[1]))
	}

	text := enc.Encode(streaming.TextDelta{Text: "hi"})
	if len(text) != 1 {
		t.Fatalf("text frames = %v", text)
	}
	event := frame(t, text[0])
	if event["type"] != "content_block_delta" {
		t.Fatalf("event = %v", event)
	}
	if event["delta"].(map[string]any)["text"] != "hi" {
		t.Fatalf("delta = %v", event["delta"])
	}

	enc.Encode(streaming.UsageFinal{Prompt: 12, Output: 3, Cached: 2})
	enc.Encode(streaming.Finish{StopReason: ir.StopStop})
	end := enc.End()

	var messageDelta, messageStop bool
	for _, payload := range end {
		event := frame(t, payload)
		switch event["type"] {
		case "message_delta":
			messageDelta = true
			delta := event["delta"].(map[string]any)
			if delta["stop_reason"] != "end_turn" {
				t.Fatalf("stop_reason = %v", delta["stop_reason"])
			}
			usage := event["usage"].(map[string]any)
			if usage["output_tokens"] != float64(3) {
				t.Fatalf("usage = %v", usage)
			}
		case "message_stop":
			messageStop = true
		}
	}
	if !messageDelta || !messageStop {
		t.Fatalf("terminal events missing: %v", end)
	}
}

func TestMessagesStreamEncoderToolUse(t *testing.T) {
	enc := NewStreamEncoder(SurfaceMessages, "claude-sonnet-4", "req4")
	enc.Start()
	open := enc.Encode(streaming.ToolCallOpen{Index: 0, ID: "tu_1", Name: "f"})
	var blockStart map[string]any
	for _, payload := range open {
		event := frame(t, payload)
		if event["type"] == "content_block_start" {
			blockStart = event
		}
	}
	if blockStart == nil {
		t.Fatalf("no content_block_start: %v", open)
	}
	block := blockStart["content_block"].(map[string]any)
	if block["type"] != "tool_use" || block["id"] != "tu_1" {
		t.Fatalf("tool block = %v", block)
	}
	args := enc.Encode(streaming.ArgsDelta{Index: 0, ArgsFragment: `{"a":`})
	if len(args) != 1 {
		t.Fatalf("args frames = %v", args)
	}
	delta := frame(t, args[0])["delta"].(map[string]any)
	if delta["type"] != "input_json_delta" || delta["partial_json"] != `{"a":` {
		t.Fatalf("args delta = %v", delta)
	}
	closeFrames := enc.Encode(streaming.ToolCallClose{Index: 0})
	if len(closeFrames) != 1 || frame(t, closeFrames[0])["type"] != "content_block_stop" {
		t.Fatalf("close frames = %v", closeFrames)
	}
	enc.Encode(streaming.Finish{StopReason: ir.StopToolCall})
	end := enc.End()
	last := frame(t, end[len(end)-1])
	if last["type"] != "message_stop" {
		t.Fatalf("terminal = %v", last)
	}
}

func TestCompletionsStreamEncoder(t *testing.T) {
	enc := NewStreamEncoder(SurfaceCompletions, "m", "req5")
	enc.Start()
	frames := enc.Encode(streaming.TextDelta{Text: "hey"})
	if len(frames) != 1 {
		t.Fatalf("frames = %v", frames)
	}
	choice := frame(t, frames[0])["choices"].([]any)[0].(map[string]any)
	if choice["text"] != "hey" {
		t.Fatalf("text = %v", choice["text"])
	}
	enc.Encode(streaming.Finish{StopReason: ir.StopStop})
	end := enc.End()
	if end[len(end)-1] != "[DONE]" {
		t.Fatalf("terminal = %q", end[len(end)-1])
	}
}

func TestResponsesStreamEncoder(t *testing.T) {
	enc := NewStreamEncoder(SurfaceResponses, "gpt-5", "req6")
	start := enc.Start()
	if len(start) != 1 || frame(t, start[0])["type"] != "response.created" {
		t.Fatalf("start = %v", start)
	}
	deltaFrames := enc.Encode(streaming.TextDelta{Text: "hey"})
	var sawDelta bool
	for _, payload := range deltaFrames {
		if frame(t, payload)["type"] == "response.output_text.delta" {
			sawDelta = true
		}
	}
	if !sawDelta {
		t.Fatalf("no output_text.delta: %v", deltaFrames)
	}
	enc.Encode(streaming.UsageFinal{Prompt: 5, Output: 1})
	enc.Encode(streaming.Finish{StopReason: ir.StopStop})
	end := enc.End()
	terminal := frame(t, end[len(end)-1])
	if terminal["type"] != "response.completed" {
		t.Fatalf("terminal = %v", terminal)
	}
	resp := terminal["response"].(map[string]any)
	if resp["status"] != "completed" {
		t.Fatalf("status = %v", resp["status"])
	}
	if len(resp["output"].([]any)) != 1 {
		t.Fatalf("output = %v", resp["output"])
	}
}

func TestStreamErrorTerminatesWithoutTerminalSuccess(t *testing.T) {
	// Chat: the error frame is terminal — no finish chunk may follow.
	chat := NewStreamEncoder(SurfaceChat, "m", "r")
	chat.Start()
	errFrames := chat.Encode(streaming.StreamError{Message: "upstream died", Status: 529})
	if len(errFrames) != 1 {
		t.Fatalf("chat error frames = %v", errFrames)
	}
	if frame(t, errFrames[0])["error"].(map[string]any)["type"] != "overloaded_error" {
		t.Fatalf("chat error = %v", errFrames[0])
	}
	if end := chat.End(); len(end) != 1 || end[0] != "[DONE]" {
		t.Fatalf("chat End after error = %v", end)
	}

	// Responses: response.failed is terminal, End adds nothing.
	resp := NewStreamEncoder(SurfaceResponses, "m", "r")
	resp.Start()
	resp.Encode(streaming.TextDelta{Text: "partial"})
	respErr := resp.Encode(streaming.StreamError{Message: "boom", Status: 500})
	last := frame(t, respErr[len(respErr)-1])
	if last["type"] != "response.failed" {
		t.Fatalf("responses terminal = %v", last)
	}
	if end := resp.End(); len(end) != 0 {
		t.Fatalf("responses End after error = %v", end)
	}

	// Messages: open blocks are closed before the error event.
	msg := NewStreamEncoder(SurfaceMessages, "m", "r")
	msg.Start()
	msg.Encode(streaming.TextDelta{Text: "partial"})
	msgErr := msg.Encode(streaming.StreamError{Message: "boom", Status: 429})
	var sawStop, sawError bool
	for _, payload := range msgErr {
		switch frame(t, payload)["type"] {
		case "content_block_stop":
			sawStop = true
		case "error":
			sawError = true
		}
	}
	if !sawStop || !sawError {
		t.Fatalf("messages error frames = %v", msgErr)
	}
}

func TestStreamEncodersNeverPanicOnUnknownDeltas(t *testing.T) {
	for _, s := range []Surface{SurfaceChat, SurfaceMessages, SurfaceResponses, SurfaceCompletions} {
		enc := NewStreamEncoder(s, "m", "r")
		enc.Start()
		enc.Encode(streaming.LogprobsDelta{Logprobs: map[string]any{"t": 1}})
		enc.Encode(streaming.ServerToolResultDelta{Index: 0, Block: map[string]any{"x": 1}})
		enc.Encode(streaming.ToolCallArgsDelta{Index: 7, ArgsFragment: "{}"})
		enc.Encode(streaming.ToolCallClose{Index: 7})
		enc.Encode(streaming.StreamEnd{})
		if end := enc.End(); len(end) == 0 {
			t.Fatalf("%s: End returned nothing", s)
		}
	}
}

// --- error bodies -----------------------------------------------------------

func TestErrorBodyPerDialect(t *testing.T) {
	for _, s := range []Surface{SurfaceChat, SurfaceCompletions} {
		status, body := ErrorBody(s, 429, "rate_limit_error", "slow down")
		if status != 429 {
			t.Fatalf("%s status = %d", s, status)
		}
		errObj := body["error"].(map[string]any)
		if errObj["message"] != "slow down" || errObj["type"] != "rate_limit_error" ||
			errObj["code"] != "rate_limit_error" {
			t.Fatalf("%s body = %v", s, body)
		}
	}

	_, responses := ErrorBody(SurfaceResponses, 500, "api_error", "boom")
	if responses["error"].(map[string]any)["code"] != "api_error" {
		t.Fatalf("responses body = %v", responses)
	}
	if _, ok := responses["error"].(map[string]any)["param"]; !ok {
		t.Fatalf("responses body should carry param: %v", responses)
	}

	cases := []struct {
		status int
		eType  string
		want   string
	}{
		{429, "rate_limit_error", "rate_limit_error"},
		{529, "service_unavailable", "overloaded_error"},
		{529, "", "overloaded_error"},
		{401, "", "authentication_error"},
		{400, "", "invalid_request_error"},
		{504, "timeout", "timeout_error"},
		{402, "budget_exceeded", "permission_error"},
		{500, "", "api_error"},
	}
	for _, c := range cases {
		status, body := ErrorBody(SurfaceMessages, c.status, c.eType, "bad")
		if status != c.status {
			t.Fatalf("messages status = %d", status)
		}
		if body["type"] != "error" {
			t.Fatalf("messages body = %v", body)
		}
		got := body["error"].(map[string]any)["type"]
		if got != c.want {
			t.Fatalf("status %d etype %q -> %v, want %v", c.status, c.eType, got, c.want)
		}
	}
}

// --- misc -------------------------------------------------------------------

func TestDecodeRequestRejectsBadSurface(t *testing.T) {
	if _, err := DecodeRequest(Surface("bogus"), map[string]any{"model": "m"}); err == nil {
		t.Fatal("unknown surface should error")
	}
	if _, err := DecodeRequest(SurfaceChat, nil); err == nil {
		t.Fatal("nil body should error")
	}
}

func TestOpenAIUsageConvention(t *testing.T) {
	cachedIn := true
	body := EncodeResponse(SurfaceChat, ir.AssistantTurn{
		Text: "x", StopReason: ir.StopStop,
		Usage: ir.Usage{PromptTokens: 10, CompletionTokens: 2, CachedTokens: 4, CachedInPrompt: &cachedIn},
	}, "m", ir.Request{})
	usage := body["usage"].(map[string]any)
	if usage["prompt_tokens"] != 10 {
		t.Fatalf("cached_in_prompt prompt_tokens = %v", usage)
	}

	body = EncodeResponse(SurfaceChat, ir.AssistantTurn{
		Text: "x", StopReason: ir.StopStop,
		Usage: ir.Usage{PromptTokens: 10, CompletionTokens: 2, CachedTokens: 4},
	}, "m", ir.Request{})
	usage = body["usage"].(map[string]any)
	if usage["prompt_tokens"] != 14 {
		t.Fatalf("exclusive prompt_tokens = %v", usage)
	}
}

func TestToolFinishDowngrade(t *testing.T) {
	body := EncodeResponse(SurfaceChat, ir.AssistantTurn{
		StopReason: ir.StopToolCall,
	}, "m", ir.Request{})
	choice := body["choices"].([]any)[0].(map[string]any)
	if choice["finish_reason"] != "stop" {
		t.Fatalf("tool_calls with no calls should downgrade: %v", choice["finish_reason"])
	}
}
