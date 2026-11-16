package providers

import (
	"encoding/json"
	"strings"
	"testing"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

func intp(v int) *int         { return &v }
func f64p(v float64) *float64 { return &v }
func boolp(v bool) *bool      { return &v }
func strp(v string) *string   { return &v }

func decodeJSONBody(t *testing.T, body []byte) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatalf("invalid fixture JSON: %v", err)
	}
	return out
}

func stringList(v any) []string {
	switch x := v.(type) {
	case []string:
		return x
	case []any:
		out := make([]string, 0, len(x))
		for _, e := range x {
			if s, ok := e.(string); ok {
				out = append(out, s)
			}
		}
		return out
	}
	return nil
}

func deltaNames(deltas []streaming.Delta) []string {
	names := make([]string, 0, len(deltas))
	for _, d := range deltas {
		switch d.(type) {
		case streaming.StreamStart:
			names = append(names, "start")
		case streaming.TextDelta:
			names = append(names, "text")
		case streaming.ThinkingDelta:
			names = append(names, "thinking")
		case streaming.ToolCallOpen:
			names = append(names, "open")
		case streaming.ToolCallArgsDelta:
			names = append(names, "args")
		case streaming.ToolCallClose:
			names = append(names, "close")
		case streaming.UsageFinal:
			names = append(names, "usage")
		case streaming.Finish:
			names = append(names, "finish")
		case streaming.StreamEnd:
			names = append(names, "end")
		case streaming.StreamError:
			names = append(names, "error")
		default:
			names = append(names, "other")
		}
	}
	return names
}

func mustFirstMessage(t *testing.T, body map[string]any, i int) map[string]any {
	t.Helper()
	msgs := AsList(body["messages"])
	if len(msgs) <= i {
		t.Fatalf("messages[%d] missing (have %d)", i, len(msgs))
	}
	return AsDict(msgs[i])
}

// -- OpenAI wire -------------------------------------------------------------

func TestOpenAIEncodeRequest(t *testing.T) {
	a, err := FreshAdapter("openai")
	if err != nil {
		t.Fatal(err)
	}
	req := ir.Request{
		Model: "gpt-4o",
		Messages: []ir.Message{
			{Role: ir.RoleSystem, Parts: []ir.Part{ir.NewTextPart("be brief")}},
			{Role: ir.RoleUser, Parts: []ir.Part{
				ir.NewTextPart("hi"),
				ir.NewImagePart("", "QUJD", "image/png"),
			}},
			{Role: ir.RoleAssistant, Parts: []ir.Part{
				ir.NewToolUsePart("call_1", "lookup", map[string]any{"q": "x"}),
			}},
			{Role: ir.RoleTool, Parts: []ir.Part{ir.NewToolResultPart("call_1", "result", false)}},
		},
		Tools: []ir.Tool{{
			Name: "lookup", Description: "d",
			ParametersJSONSchema: map[string]any{"type": "object"},
		}},
		ToolChoice: ir.ToolChoiceRequired{},
		GenParams: ir.GenParams{
			MaxTokens: intp(100), Temperature: f64p(0.2), Stop: []string{"END"},
			Seed: intp(7), ParallelToolCalls: boolp(false),
			ReasoningEffort: strp("high"),
		},
		Stream:                    true,
		StreamOptionsIncludeUsage: true,
		Extras:                    map[string]any{"user": "u1"},
	}
	params := map[string]any{
		"max_tokens":    999,
		"provider_type": "openai",
		"extra_body":    map[string]any{"top_k": 5},
	}
	body, err := a.EncodeRequest(req, "gpt-4o", params)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if body["model"] != "gpt-4o" {
		t.Errorf("model = %v", body["model"])
	}
	if body["max_tokens"] != 100 {
		t.Errorf("gen max_tokens should win over params, got %v", body["max_tokens"])
	}
	if body["temperature"] != 0.2 {
		t.Errorf("temperature = %v", body["temperature"])
	}
	if body["seed"] != 7 {
		t.Errorf("seed = %v", body["seed"])
	}
	if body["parallel_tool_calls"] != false {
		t.Errorf("parallel_tool_calls = %v", body["parallel_tool_calls"])
	}
	if body["reasoning_effort"] != "high" {
		t.Errorf("reasoning_effort = %v", body["reasoning_effort"])
	}
	if body["stream"] != true {
		t.Errorf("stream = %v", body["stream"])
	}
	if body["tool_choice"] != "required" {
		t.Errorf("tool_choice = %v", body["tool_choice"])
	}
	if body["user"] != "u1" {
		t.Errorf("extras not merged: user = %v", body["user"])
	}
	if body["top_k"] != 5 {
		t.Errorf("extra_body not merged: top_k = %v", body["top_k"])
	}
	if _, leaked := body["provider_type"]; leaked {
		t.Errorf("control param provider_type leaked into the body")
	}
	if so := AsDict(body["stream_options"]); so["include_usage"] != true {
		t.Errorf("stream_options.include_usage = %v", so["include_usage"])
	}
	if stop := stringList(body["stop"]); len(stop) != 1 || stop[0] != "END" {
		t.Errorf("stop = %v", body["stop"])
	}

	sys := mustFirstMessage(t, body, 0)
	if sys["role"] != "system" || sys["content"] != "be brief" {
		t.Errorf("system message = %v", sys)
	}
	user := mustFirstMessage(t, body, 1)
	contentParts := AsList(user["content"])
	if len(contentParts) != 2 {
		t.Fatalf("user content parts = %v", user["content"])
	}
	if p := AsDict(contentParts[0]); p["type"] != "text" || p["text"] != "hi" {
		t.Errorf("text part = %v", p)
	}
	img := AsDict(contentParts[1])
	imgURL := AsDict(img["image_url"])
	if imgURL["url"] != "data:image/png;base64,QUJD" {
		t.Errorf("image url = %v", imgURL["url"])
	}
	assistant := mustFirstMessage(t, body, 2)
	toolCalls := AsList(assistant["tool_calls"])
	if len(toolCalls) != 1 {
		t.Fatalf("tool_calls = %v", assistant["tool_calls"])
	}
	fn := AsDict(AsDict(toolCalls[0])["function"])
	if fn["name"] != "lookup" {
		t.Errorf("tool name = %v", fn["name"])
	}
	var args map[string]any
	if err := json.Unmarshal([]byte(fn["arguments"].(string)), &args); err != nil {
		t.Fatalf("arguments not JSON: %v", fn["arguments"])
	}
	if args["q"] != "x" {
		t.Errorf("arguments = %v", args)
	}
	toolMsg := mustFirstMessage(t, body, 3)
	if toolMsg["role"] != "tool" || toolMsg["tool_call_id"] != "call_1" || toolMsg["content"] != "result" {
		t.Errorf("tool message = %v", toolMsg)
	}
	tools := AsList(body["tools"])
	if len(tools) != 1 {
		t.Fatalf("tools = %v", body["tools"])
	}
	if AsDict(tools[0])["type"] != "function" {
		t.Errorf("tool type = %v", AsDict(tools[0])["type"])
	}
}

func TestOpenAIDecodeResponse(t *testing.T) {
	a, _ := FreshAdapter("openai")
	raw := []byte(`{
		"choices": [{
			"message": {
				"content": "hello",
				"reasoning_content": "because",
				"tool_calls": [{
					"id": "call_9", "type": "function",
					"function": {"name": "lookup", "arguments": "{\"q\":\"y\"}"}
				}]
			},
			"finish_reason": "tool_calls"
		}],
		"usage": {
			"prompt_tokens": 10, "completion_tokens": 5,
			"prompt_tokens_details": {"cached_tokens": 4},
			"completion_tokens_details": {"reasoning_tokens": 2}
		}
	}`)
	turn, err := a.DecodeResponse(200, raw)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if turn.Text != "hello" {
		t.Errorf("text = %q", turn.Text)
	}
	if len(turn.Thinking) != 1 || turn.Thinking[0].Text != "because" {
		t.Errorf("thinking = %+v", turn.Thinking)
	}
	if len(turn.ToolCalls) != 1 {
		t.Fatalf("tool calls = %+v", turn.ToolCalls)
	}
	tc := turn.ToolCalls[0]
	if tc.ID != "call_9" || tc.Name != "lookup" || tc.Args["q"] != "y" {
		t.Errorf("tool call = %+v", tc)
	}
	if turn.StopReason != ir.StopToolCall {
		t.Errorf("stop reason = %q", turn.StopReason)
	}
	if turn.Usage.PromptTokens != 10 || turn.Usage.CompletionTokens != 5 ||
		turn.Usage.CachedTokens != 4 || turn.Usage.ReasoningTokens != 2 {
		t.Errorf("usage = %+v", turn.Usage)
	}
	if turn.Usage.CachedInPrompt == nil || !*turn.Usage.CachedInPrompt {
		t.Errorf("cached_in_prompt should be true for Chat Completions")
	}
}

func TestOpenAIDecodeResponseError(t *testing.T) {
	a, _ := FreshAdapter("openai")
	_, err := a.DecodeResponse(429, []byte(`{"error":{"message":"slow down","type":"rate_limit_error"}}`))
	if err == nil {
		t.Fatal("expected an error for a 429")
	}
	we, ok := err.(*WiwiError)
	if !ok || we.Status != 429 || !we.Retryable {
		t.Fatalf("unexpected error: %#v", err)
	}
}

func TestOpenAIStreamDecode(t *testing.T) {
	a, _ := FreshAdapter("openai")
	frames := []string{
		`{"model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant","content":"Hel"}}]}`,
		`{"choices":[{"index":0,"delta":{"content":"lo"}}]}`,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup","arguments":""}}]}}]}`,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"q\":"}}]}}]}`,
		`{"choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\"z\"}"}}]}}]}`,
		`{"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":10,"completion_tokens":6}}`,
		`[DONE]`,
	}
	var all []streaming.Delta
	for _, frame := range frames {
		deltas, err := a.DecodeStreamEvent("", frame)
		if err != nil {
			t.Fatalf("decode %s: %v", frame, err)
		}
		all = append(all, deltas...)
	}
	want := []string{"start", "text", "text", "open", "args", "args", "usage", "close", "finish", "end"}
	got := deltaNames(all)
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("delta sequence\n got: %v\nwant: %v", got, want)
	}

	start := all[0].(streaming.StreamStart)
	if start.Model != "gpt-4o" {
		t.Errorf("start model = %q", start.Model)
	}
	if d := all[1].(streaming.TextDelta); d.Text != "Hel" {
		t.Errorf("first text = %q", d.Text)
	}
	open := all[3].(streaming.ToolCallOpen)
	if open.Index != 0 || open.ID != "call_1" || open.Name != "lookup" {
		t.Errorf("open = %+v", open)
	}
	if d := all[4].(streaming.ToolCallArgsDelta); d.ArgsFragment != `{"q":` {
		t.Errorf("args fragment = %q", d.ArgsFragment)
	}
	if u := all[6].(streaming.UsageFinal); u.Prompt != 10 || u.Output != 6 {
		t.Errorf("usage = %+v", u)
	}
	if f := all[8].(streaming.Finish); f.StopReason != ir.StopToolCall {
		t.Errorf("finish = %+v", f)
	}

	// Reset must drop per-stream state so the adapter can serve another stream.
	a.Reset()
	deltas, err := a.DecodeStreamEvent("", `{"choices":[{"index":0,"delta":{"content":"again"}}]}`)
	if err != nil {
		t.Fatal(err)
	}
	if names := deltaNames(deltas); len(names) != 2 || names[0] != "start" || names[1] != "text" {
		t.Fatalf("after Reset got %v", names)
	}
}

func TestOpenAIStreamDoneClosesOpenTool(t *testing.T) {
	a, _ := FreshAdapter("openai")
	if _, err := a.DecodeStreamEvent("", `{"choices":[{"delta":{"tool_calls":[{"index":2,"id":"c","function":{"name":"f","arguments":"{}"}}]}}]}`); err != nil {
		t.Fatal(err)
	}
	deltas, err := a.DecodeStreamEvent("", "[DONE]")
	if err != nil {
		t.Fatal(err)
	}
	if names := deltaNames(deltas); strings.Join(names, ",") != "close,finish,end" {
		t.Fatalf("got %v", names)
	}
	if f := deltas[1].(streaming.Finish); f.StopReason != ir.StopToolCall {
		t.Errorf("finish = %+v", f)
	}
}

// -- Anthropic ---------------------------------------------------------------

func TestAnthropicEncodeRequest(t *testing.T) {
	a, err := FreshAdapter("anthropic")
	if err != nil {
		t.Fatal(err)
	}
	req := ir.Request{
		Model: "claude-sonnet-4",
		Messages: []ir.Message{
			{Role: ir.RoleSystem, Parts: []ir.Part{ir.NewTextPart("sys one"), ir.NewTextPart("sys two")}},
			{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}},
			{Role: ir.RoleAssistant, Parts: []ir.Part{
				ir.NewToolUsePart("tu_1", "lookup", map[string]any{"q": "x"}),
			}},
			{Role: ir.RoleUser, Parts: []ir.Part{ir.NewToolResultPart("tu_1", "result", true)}},
		},
		Tools: []ir.Tool{{
			Name: "lookup", Description: "d",
			ParametersJSONSchema: map[string]any{"type": "object"},
		}},
		ToolChoice: ir.ToolChoiceNamed{Name: "lookup"},
		GenParams: ir.GenParams{
			Temperature: f64p(0.3), TopP: f64p(0.9), TopK: intp(5),
			Stop: []string{"END"}, ThinkingBudget: intp(2048),
		},
		Stream: true,
	}
	body, err := a.EncodeRequest(req, "claude-sonnet-4", nil)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	if body["system"] != "sys one\nsys two" {
		t.Errorf("system = %v", body["system"])
	}
	if body["max_tokens"] != 4096 {
		t.Errorf("default max_tokens = %v", body["max_tokens"])
	}
	if body["temperature"] != 0.3 || body["top_p"] != 0.9 || body["top_k"] != 5 {
		t.Errorf("sampling params wrong: %v", body)
	}
	if stop := stringList(body["stop_sequences"]); len(stop) != 1 || stop[0] != "END" {
		t.Errorf("stop_sequences = %v", body["stop_sequences"])
	}
	if body["stream"] != true {
		t.Errorf("stream = %v", body["stream"])
	}
	thinking := AsDict(body["thinking"])
	if thinking["type"] != "enabled" || thinking["budget_tokens"] != 2048 {
		t.Errorf("thinking = %v", thinking)
	}

	msgs := AsList(body["messages"])
	if len(msgs) != 3 {
		t.Fatalf("messages = %v", msgs)
	}
	user := AsDict(msgs[0])
	if user["role"] != "user" {
		t.Errorf("first message role = %v", user["role"])
	}
	blocks := AsList(user["content"])
	if len(blocks) != 1 || AsDict(blocks[0])["text"] != "hi" {
		t.Errorf("user blocks = %v", user["content"])
	}
	assistantBlocks := AsList(AsDict(msgs[1])["content"])
	toolUse := AsDict(assistantBlocks[0])
	if toolUse["type"] != "tool_use" || toolUse["id"] != "tu_1" || toolUse["name"] != "lookup" {
		t.Errorf("tool_use block = %v", toolUse)
	}
	if AsDict(toolUse["input"])["q"] != "x" {
		t.Errorf("tool_use input = %v", toolUse["input"])
	}
	toolResult := AsDict(AsList(AsDict(msgs[2])["content"])[0])
	if toolResult["type"] != "tool_result" || toolResult["tool_use_id"] != "tu_1" {
		t.Errorf("tool_result block = %v", toolResult)
	}
	if toolResult["is_error"] != true {
		t.Errorf("tool_result is_error = %v", toolResult["is_error"])
	}

	tools := AsList(body["tools"])
	if len(tools) != 1 || AsDict(tools[0])["name"] != "lookup" {
		t.Errorf("tools = %v", body["tools"])
	}
	if AsDict(tools[0])["input_schema"] == nil {
		t.Errorf("tool input_schema missing")
	}
	tc := AsDict(body["tool_choice"])
	if tc["type"] != "tool" || tc["name"] != "lookup" {
		t.Errorf("tool_choice = %v", tc)
	}
}

func TestAnthropicDecodeResponse(t *testing.T) {
	a, _ := FreshAdapter("anthropic")
	raw := []byte(`{
		"content": [
			{"type": "text", "text": "hi"},
			{"type": "thinking", "thinking": "why", "signature": "sig"},
			{"type": "tool_use", "id": "tu_1", "name": "lookup", "input": {"q": 1}}
		],
		"stop_reason": "tool_use",
		"stop_sequence": "STOP",
		"usage": {
			"input_tokens": 11, "output_tokens": 7,
			"cache_read_input_tokens": 3, "cache_creation_input_tokens": 2
		}
	}`)
	turn, err := a.DecodeResponse(200, raw)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if turn.Text != "hi" {
		t.Errorf("text = %q", turn.Text)
	}
	if len(turn.Thinking) != 1 || turn.Thinking[0].Text != "why" || turn.Thinking[0].Signature != "sig" {
		t.Errorf("thinking = %+v", turn.Thinking)
	}
	if len(turn.ToolCalls) != 1 || turn.ToolCalls[0].Name != "lookup" {
		t.Fatalf("tool calls = %+v", turn.ToolCalls)
	}
	if turn.ToolCalls[0].Args["q"] != float64(1) {
		t.Errorf("args = %v", turn.ToolCalls[0].Args)
	}
	if turn.StopReason != ir.StopToolCall {
		t.Errorf("stop reason = %q", turn.StopReason)
	}
	if turn.StopSequence == nil || *turn.StopSequence != "STOP" {
		t.Errorf("stop sequence = %v", turn.StopSequence)
	}
	if turn.Usage.PromptTokens != 11 || turn.Usage.CompletionTokens != 7 ||
		turn.Usage.CachedTokens != 3 || turn.Usage.CacheCreationTokens != 2 {
		t.Errorf("usage = %+v", turn.Usage)
	}
	if turn.Usage.CachedInPrompt == nil || *turn.Usage.CachedInPrompt {
		t.Errorf("cached_in_prompt should be false for the Messages API")
	}
}

func TestAnthropicStreamDecode(t *testing.T) {
	a, _ := FreshAdapter("anthropic")
	frames := []string{
		`{"type":"message_start","message":{"model":"claude-sonnet-4","usage":{"input_tokens":12,"cache_read_input_tokens":4}}}`,
		`{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"tu_1","name":"lookup","input":{}}}`,
		`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"q\":"}}`,
		`{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"1}"}}`,
		`{"type":"content_block_stop","index":0}`,
		`{"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":3}}`,
		`{"type":"message_stop"}`,
	}
	var all []streaming.Delta
	for _, frame := range frames {
		deltas, err := a.DecodeStreamEvent("", frame)
		if err != nil {
			t.Fatalf("decode: %v", err)
		}
		all = append(all, deltas...)
	}
	want := "start,open,args,args,close,usage,finish,end"
	if got := strings.Join(deltaNames(all), ","); got != want {
		t.Fatalf("delta sequence\n got: %s\nwant: %s", got, want)
	}
	start := all[0].(streaming.StreamStart)
	if start.Model != "claude-sonnet-4" || start.Prompt != 12 || start.Cached != 4 {
		t.Errorf("start = %+v", start)
	}
	if open := all[1].(streaming.ToolCallOpen); open.ID != "tu_1" || open.Name != "lookup" || open.BlockType != "tool_use" {
		t.Errorf("open = %+v", open)
	}
	if f := all[6].(streaming.Finish); f.StopReason != ir.StopToolCall {
		t.Errorf("finish = %+v", f)
	}
}

func TestAnthropicPingIsSilent(t *testing.T) {
	a, _ := FreshAdapter("anthropic")
	deltas, err := a.DecodeStreamEvent("ping", `{"type":"ping"}`)
	if err != nil {
		t.Fatal(err)
	}
	// The first frame still opens the stream legally; a later ping adds nothing.
	if len(deltas) != 1 {
		t.Fatalf("got %v", deltaNames(deltas))
	}
	deltas, _ = a.DecodeStreamEvent("ping", `{"type":"ping"}`)
	if len(deltas) != 0 {
		t.Fatalf("second ping emitted %v", deltaNames(deltas))
	}
}

// -- Gemini ------------------------------------------------------------------

func TestGeminiEncodeAndDecode(t *testing.T) {
	a, err := FreshAdapter("gemini")
	if err != nil {
		t.Fatal(err)
	}
	req := ir.Request{
		Model: "gemini-2.5-pro",
		Messages: []ir.Message{
			{Role: ir.RoleSystem, Parts: []ir.Part{ir.NewTextPart("be nice")}},
			{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}},
		},
		GenParams: ir.GenParams{MaxTokens: intp(64), Temperature: f64p(0.1)},
	}
	body, err := a.EncodeRequest(req, "gemini-2.5-pro", nil)
	if err != nil {
		t.Fatalf("encode: %v", err)
	}
	sys := AsDict(body["systemInstruction"])
	sysParts := AsList(sys["parts"])
	if len(sysParts) != 1 || AsDict(sysParts[0])["text"] != "be nice" {
		t.Errorf("systemInstruction = %v", sys)
	}
	contents := AsList(body["contents"])
	if len(contents) != 1 || AsDict(contents[0])["role"] != "user" {
		t.Fatalf("contents = %v", contents)
	}
	gen := AsDict(body["generationConfig"])
	if gen["maxOutputTokens"] != 64 || gen["temperature"] != 0.1 {
		t.Errorf("generationConfig = %v", gen)
	}

	raw := []byte(`{
		"candidates": [{"content": {"parts": [{"text": "hello"}]}, "finishReason": "STOP"}],
		"usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2, "thoughtsTokenCount": 1}
	}`)
	turn, err := a.DecodeResponse(200, raw)
	if err != nil {
		t.Fatalf("decode: %v", err)
	}
	if turn.Text != "hello" || turn.StopReason != ir.StopStop {
		t.Errorf("turn = %+v", turn)
	}
	if turn.Usage.PromptTokens != 5 || turn.Usage.CompletionTokens != 3 || turn.Usage.ReasoningTokens != 1 {
		t.Errorf("usage = %+v", turn.Usage)
	}
}

func TestGeminiBuildURL(t *testing.T) {
	a, _ := FreshAdapter("gemini")
	if got := a.BuildURL("https://g.example/v1beta", "m1", false); got != "https://g.example/v1beta/models/m1:generateContent" {
		t.Errorf("non-stream url = %q", got)
	}
	if got := a.BuildURL("https://g.example/v1beta", "m1", true); got != "https://g.example/v1beta/models/m1:streamGenerateContent?alt=sse" {
		t.Errorf("stream url = %q", got)
	}
}

// -- registry ----------------------------------------------------------------

func TestAllProviderTypesResolve(t *testing.T) {
	all := []string{
		"openai", "anthropic", "gemini", "openai-compatible", "openrouter",
		"gmicloud", "bai", "nvidia-nim", "cline", "workbuddy", "opencode",
		"hermes", "ifm",
	}
	for _, name := range all {
		a, err := FreshAdapter(name)
		if err != nil {
			t.Errorf("FreshAdapter(%q): %v", name, err)
			continue
		}
		if got := a.ProviderType(); got != name {
			t.Errorf("FreshAdapter(%q).ProviderType() = %q", name, got)
		}
	}
	if missing := MissingProviders(all); len(missing) != 0 {
		t.Errorf("missing adapters: %v", missing)
	}
}

func TestAdapterHeadersAndURLs(t *testing.T) {
	openai, _ := FreshAdapter("openai")
	h := openai.Headers(ProviderKeyRef{Label: "k", Secret: "sk-1"})
	if h["Authorization"] != "Bearer sk-1" || h["Content-Type"] != "application/json" {
		t.Errorf("openai headers = %v", h)
	}
	if got := openai.BuildURL("https://api.example/v1/", "m", false); got != "https://api.example/v1/chat/completions" {
		t.Errorf("openai url = %q", got)
	}

	anthropic, _ := FreshAdapter("anthropic")
	h = anthropic.Headers(ProviderKeyRef{Secret: "sk-ant"})
	if h["x-api-key"] != "sk-ant" || h["anthropic-version"] != "2023-06-01" {
		t.Errorf("anthropic headers = %v", h)
	}
	if got := anthropic.BuildURL("https://api.anthropic.com", "m", true); got != "https://api.anthropic.com/v1/messages" {
		t.Errorf("anthropic url = %q", got)
	}

	gemini, _ := FreshAdapter("gemini")
	if h := gemini.Headers(ProviderKeyRef{Secret: "g-key"}); h["x-goog-api-key"] != "g-key" {
		t.Errorf("gemini headers = %v", h)
	}
}

func TestOpenAICompatibleSuppressesReasoningEffort(t *testing.T) {
	a, _ := FreshAdapter("openai-compatible")
	req := ir.Request{
		Messages:  []ir.Message{{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}}},
		GenParams: ir.GenParams{ReasoningEffort: strp("high")},
	}
	body, err := a.EncodeRequest(req, "m", nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := body["reasoning_effort"]; ok {
		t.Errorf("strict gateways reject reasoning_effort; body has it: %v", body["reasoning_effort"])
	}
}
