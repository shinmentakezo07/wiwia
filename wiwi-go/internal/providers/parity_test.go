package providers

import (
	"encoding/json"
	"strings"
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
		"muse-ember-1": "responses", "muse-wonder-1": "responses",
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
	// The default OpenAI root is treated as unset, so each wire gets its own
	// canonical base (Python _ROUTE_BASES).
	if got := a.BuildURL(opencodeDefaultBase, "claude-x", false); got != "https://opencode.ai/inference/anthropic/v1/messages" {
		t.Fatalf("canonical messages url: %s", got)
	}
	if got := a.BuildURL(opencodeDefaultBase, "gemini-2.0", false); !strings.HasPrefix(got, "https://opencode.ai/inference/google/v1beta/models/") {
		t.Fatalf("canonical gemini url: %s", got)
	}
	// Credential scheme follows the wire, not the adapter.
	a.modelID = "claude-x"
	if h := a.Headers(ProviderKeyRef{Label: "k", Secret: "sk"}); h["x-api-key"] != "sk" || h["Authorization"] != "" {
		t.Fatalf("messages headers: %v", h)
	}
	a.modelID = "gemini-2.0"
	if h := a.Headers(ProviderKeyRef{Label: "k", Secret: "sk"}); h["x-goog-api-key"] != "sk" {
		t.Fatalf("gemini headers: %v", h)
	}
	a.modelID = "gpt-4o"
	if h := a.Headers(ProviderKeyRef{Label: "k", Secret: "sk"}); h["Authorization"] != "Bearer sk" {
		t.Fatalf("chat headers: %v", h)
	}
	if h := a.Headers(ProviderKeyRef{Label: "k", Secret: "anonymous"}); h["Authorization"] != "Bearer public" {
		t.Fatalf("anonymous headers: %v", h)
	}
	reqWithMeta := ir.Request{
		Model: "gpt-4o", Stream: false,
		Extras: map[string]any{"metadata": map[string]any{"user_id": "u"}},
	}
	body, err := a.EncodeRequest(reqWithMeta, "gpt-4o", nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := body["metadata"]; ok {
		t.Fatalf("OpenCode must drop caller metadata: %v", body["metadata"])
	}
	body, err = a.EncodeRequest(reqWithMeta, "gpt-4o", map[string]any{"metadata": map[string]any{"x": 1}})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := body["metadata"]; !ok {
		t.Fatal("explicit extra_body metadata must be preserved")
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

func TestDeepSeekReasoningRoundtrip(t *testing.T) {
	eff := "high"
	req := ir.Request{
		Model: "deepseek-v4",
		Messages: []ir.Message{
			{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}},
			{Role: ir.RoleAssistant, Parts: []ir.Part{ir.NewTextPart("a"), ir.NewThinkingPart("because")}},
			{Role: ir.RoleAssistant, Parts: []ir.Part{ir.NewTextPart("b")}}, // reasoning dropped
			{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("again")}},
		},
		Tools:     []ir.Tool{{Name: "f", Description: "d", ParametersJSONSchema: map[string]any{"type": "object"}}},
		GenParams: ir.GenParams{ReasoningEffort: &eff},
	}
	a := &OpenAIAdapter{providerType: "openai"}
	body, err := a.EncodeRequest(req, "deepseek-v4", nil)
	if err != nil {
		t.Fatal(err)
	}
	assistant := 0
	for _, raw := range body["messages"].([]any) {
		m := raw.(map[string]any)
		if m["role"] != "assistant" {
			continue
		}
		assistant++
		if _, ok := m["reasoning_content"]; !ok {
			t.Fatalf("assistant turn missing reasoning_content: %v", m)
		}
	}
	if assistant != 2 {
		t.Fatalf("expected 2 assistant turns, got %d", assistant)
	}

	// Without tools the rule does not apply: history reasoning is stripped.
	req.Tools = nil
	body, _ = a.EncodeRequest(req, "deepseek-v4", nil)
	for _, raw := range body["messages"].([]any) {
		m := raw.(map[string]any)
		if m["role"] == "assistant" {
			if _, ok := m["reasoning_content"]; ok {
				t.Fatalf("reasoning_content should be stripped without tools: %v", m)
			}
		}
	}

	// A non-DeepSeek model is left untouched: only the turn that actually had
	// reasoning carries reasoning_content (no empty-string padding).
	req.Tools = []ir.Tool{{Name: "f", ParametersJSONSchema: map[string]any{"type": "object"}}}
	body, _ = a.EncodeRequest(req, "gpt-4o", nil)
	withReasoning := 0
	for _, raw := range body["messages"].([]any) {
		if m := raw.(map[string]any); m["role"] == "assistant" {
			if _, ok := m["reasoning_content"]; ok {
				withReasoning++
			}
		}
	}
	if withReasoning != 1 {
		t.Fatalf("non-deepseek: expected only the reasoned turn to carry reasoning_content, got %d", withReasoning)
	}
}

func TestDeepSeekThinkingDisabled(t *testing.T) {
	none := "none"
	req := ir.Request{
		Model: "deepseek-v4",
		Messages: []ir.Message{
			{Role: ir.RoleAssistant, Parts: []ir.Part{ir.NewTextPart("a")}},
		},
		Tools:     []ir.Tool{{Name: "f", ParametersJSONSchema: map[string]any{"type": "object"}}},
		GenParams: ir.GenParams{ReasoningEffort: &none},
	}
	a := &OpenAIAdapter{providerType: "openai"}
	body, _ := a.EncodeRequest(req, "deepseek-v4", nil)
	for _, raw := range body["messages"].([]any) {
		if m := raw.(map[string]any); m["role"] == "assistant" {
			if _, ok := m["reasoning_content"]; ok {
				t.Fatalf("thinking was disabled; reasoning_content must not be padded: %v", m)
			}
		}
	}
}
