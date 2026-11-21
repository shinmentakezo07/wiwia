package server

import (
	"strings"
	"testing"

	"wiwi-go/internal/ir"
)

func TestGetResponseHidesStoredInput(t *testing.T) {
	app, _ := newTestApp(t)
	if err := app.State.Responses.Save("resp_x", "master", "gpt-test", map[string]any{
		"id": "resp_x", "object": "response", "output": []any{}, "_input": "secret question",
	}); err != nil {
		t.Fatal(err)
	}
	w := do(t, app, "GET", "/v1/responses/resp_x", "")
	if w.Code != 200 {
		t.Fatalf("get: %d %s", w.Code, w.Body.String())
	}
	body := w.Body.String()
	if strings.Contains(body, "_input") || strings.Contains(body, "secret question") {
		t.Fatalf("internal input leaked: %s", body)
	}
}

func TestResponseHistoryKeepsToolItems(t *testing.T) {
	stored := map[string]any{
		"_input": []any{
			map[string]any{"type": "message", "role": "user", "content": "hi"},
			map[string]any{"type": "function_call_output", "call_id": "c1", "output": "42"},
		},
		"output": []any{
			map[string]any{"type": "function_call", "call_id": "c1", "name": "f", "arguments": `{}`},
		},
	}
	msgs := responseHistoryMessages(stored, "m")
	if len(msgs) < 3 {
		t.Fatalf("history messages = %d, want user + tool result + tool call", len(msgs))
	}
	var calls, results int
	for _, m := range msgs {
		for _, p := range m.Parts {
			switch p.Type {
			case ir.PartToolUse:
				calls++
			case ir.PartToolResult:
				results++
			}
		}
	}
	if calls != 1 || results != 1 {
		t.Fatalf("tool parts = %d calls / %d results, want 1/1", calls, results)
	}
}
