package providers

import (
	"encoding/json"
	"testing"

	"wiwi-go/internal/ir"
)

func TestResponsesAdapterEncodeDecode(t *testing.T) {
	a := &ResponsesAdapter{}
	req := ir.Request{Model: "gpt-x", Messages: []ir.Message{
		{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}},
		{Role: ir.RoleAssistant, Parts: []ir.Part{ir.NewToolUsePart("c1", "f", map[string]any{"x": 1})}},
		{Role: ir.RoleTool, Parts: []ir.Part{ir.NewToolResultPart("c1", "ok", false)}},
	}}
	body, err := a.EncodeRequest(req, "gpt-x", nil)
	if err != nil {
		t.Fatal(err)
	}
	if body["model"] != "gpt-x" || body["input"] == nil {
		t.Fatalf("body: %v", body)
	}
	raw, _ := json.Marshal(map[string]any{
		"output": []any{
			map[string]any{"type": "message", "content": []any{map[string]any{"type": "output_text", "text": "hello"}}},
			map[string]any{"type": "function_call", "call_id": "c1", "name": "f", "arguments": `{"x":1}`},
		},
		"usage": map[string]any{"input_tokens": 3, "output_tokens": 2},
	})
	turn, err := a.DecodeResponse(200, raw)
	if err != nil {
		t.Fatal(err)
	}
	if turn.Text != "hello" || len(turn.ToolCalls) != 1 || turn.Usage.PromptTokens != 3 {
		t.Fatalf("turn: %+v", turn)
	}
}

func TestResponsesStreamEvents(t *testing.T) {
	a := &ResponsesAdapter{}
	var deltas int
	for _, ev := range []string{
		`{"type":"response.created"}`,
		`{"type":"response.output_text.delta","delta":"hi"}`,
		`{"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":1}}}`,
	} {
		ds, _ := a.DecodeStreamEvent("", ev)
		deltas += len(ds)
	}
	if deltas < 4 {
		t.Fatalf("expected start+text+usage+finish+end, got %d deltas", deltas)
	}
}

func TestOpencodeRouting(t *testing.T) {
	cases := map[string]string{
		"gpt-4o": "responses", "grok-2": "responses", "muse-spark-1": "responses",
		"claude-sonnet": "messages", "qwen3.7-max": "messages", "qwen3.8-flash": "messages",
		"gemini-2.0": "gemini", "deepseek-chat": "chat", "qwen3.8-max": "chat",
	}
	for model, want := range cases {
		if got := opencodeRoute(model); got != want {
			t.Fatalf("%s: route %q want %q", model, got, want)
		}
	}
	a := &OpencodeAdapter{}
	if got := a.BuildURL("https://x/v1", "gpt-4o", false); got != "https://x/v1/responses" {
		t.Fatalf("responses url: %s", got)
	}
	if got := a.BuildURL("https://x/v1", "claude-x", false); got != "https://x/v1/messages" {
		t.Fatalf("messages url: %s", got)
	}
}

func TestIFMTransform(t *testing.T) {
	effort := "high"
	body := map[string]any{"reasoning_effort": "high", "stop": []any{"a", "b", "c", "d", "e"}, "store": true}
	applyIFM(body, ir.GenParams{ReasoningEffort: &effort})
	if _, ok := body["reasoning_effort"]; ok {
		t.Fatal("reasoning_effort must move into chat_template_kwargs")
	}
	chat := body["chat_template_kwargs"].(map[string]any)
	if chat["reasoning_effort"] != "high" {
		t.Fatalf("chat kwargs: %v", chat)
	}
	if len(body["stop"].([]any)) != 4 {
		t.Fatal("stop must be capped at 4")
	}
	if _, ok := body["store"]; ok {
		t.Fatal("store must be dropped for IFM")
	}
}

func TestNIMMemo(t *testing.T) {
	tools := []any{map[string]any{"type": "function", "function": map[string]any{
		"name": "f", "parameters": map[string]any{"type": "object", "properties": map[string]any{"type": map[string]any{"type": "string"}}},
	}}}
	_, a1 := sanitizeNIMToolsCached(tools)
	_, a2 := sanitizeNIMToolsCached(tools)
	if len(a1["f"]) == 0 || len(a2["f"]) == 0 {
		t.Fatalf("memo lost aliases: %v %v", a1, a2)
	}
}

func TestNimNativeToolMarkup(t *testing.T) {
	ns := "]<]minimax[>["
	block := ns + `<invoke name="search">` +
		ns + `<query>hello` + ns + `</query>` +
		ns + `</invoke>`
	schemas := map[string]map[string]any{"search": {"properties": map[string]any{"query": map[string]any{"type": "string"}}}}
	calls, err := parseNimToolBlock(block, schemas, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(calls) != 1 || calls[0].name != "search" || calls[0].args["query"] != "hello" {
		t.Fatalf("calls: %+v", calls)
	}
	// The framer separates visible text from the tool block.
	f := &miniMaxFramer{}
	visible, done, ok := f.Feed("before " + ns + "<tool_call>" + block + ns + "</tool_call>")
	if visible != "before " || !ok || done == "" {
		t.Fatalf("framer: visible=%q done=%v block=%q", visible, ok, done)
	}
}
