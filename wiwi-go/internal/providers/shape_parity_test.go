package providers

import (
	"strings"
	"testing"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

func TestWorkBuddyPrependsAndSanitizes(t *testing.T) {
	a := &OpenAIAdapter{providerType: "workbuddy"}
	req := ir.Request{
		Model: "wb-model",
		Messages: []ir.Message{
			{Role: ir.RoleSystem, Parts: []ir.Part{ir.NewTextPart("You are Claude Code, Anthropic's official CLI for Claude.\ncc_entrypoint=cli; cc_version=1.2; Main branch (you will usually use this for PRs)")}},
			{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}},
		},
	}
	body, err := a.EncodeRequest(req, "wb-model", nil)
	if err != nil {
		t.Fatal(err)
	}
	msgs := body["messages"].([]any)
	if len(msgs) != 2 {
		t.Fatalf("system-leading request must not be prepended: %d messages", len(msgs))
	}
	content := AsString(AsDict(msgs[0])["content"])
	for _, bad := range []string{"official CLI for Claude", "cc_entrypoint", "cc_version", "Main branch ("} {
		if strings.Contains(content, bad) {
			t.Fatalf("fingerprint %q survived: %q", bad, content)
		}
	}
	if !strings.Contains(content, "official CLI tool for Claude") ||
		!strings.Contains(content, "Default branch (") {
		t.Fatalf("rewrites missing: %q", content)
	}

	req.Messages = req.Messages[1:]
	body, err = a.EncodeRequest(req, "wb-model", nil)
	if err != nil {
		t.Fatal(err)
	}
	msgs = body["messages"].([]any)
	if len(msgs) != 2 || AsString(AsDict(msgs[0])["role"]) != "system" ||
		AsString(AsDict(msgs[0])["content"]) != workBuddyDefaultSystem {
		t.Fatalf("expected a default system message first: %v", msgs)
	}
}

func TestWorkBuddyToolChoiceNormalization(t *testing.T) {
	cases := []struct {
		name    string
		tc      ir.ToolChoice
		want    any
		tools   bool
		dropped bool
	}{
		{name: "named", tc: ir.ToolChoiceNamed{Name: "lookup"}, want: "lookup", tools: true},
		{name: "required", tc: ir.ToolChoiceRequired{}, want: "required", tools: true},
		{name: "none", tc: ir.ToolChoiceNone{}, dropped: true},
		{name: "auto", tc: ir.ToolChoiceAuto{}, want: "auto", tools: true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			a := &OpenAIAdapter{providerType: "workbuddy"}
			req := ir.Request{
				Model:      "wb-model",
				Messages:   []ir.Message{{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}}},
				ToolChoice: c.tc,
				Tools:      []ir.Tool{{Name: "lookup", ParametersJSONSchema: map[string]any{"type": "object"}}},
			}
			body, err := a.EncodeRequest(req, "wb-model", nil)
			if err != nil {
				t.Fatal(err)
			}
			got, ok := body["tool_choice"]
			if c.dropped {
				if ok {
					t.Fatalf("tool_choice must be dropped, got %v", got)
				}
				if _, ok := body["tools"]; ok {
					t.Fatal("tools must be suppressed with tool_choice=none")
				}
				return
			}
			if !ok || got != c.want {
				t.Fatalf("tool_choice = %v, want %v", got, c.want)
			}
		})
	}
}

func TestOpencodeFreeChatDecoys(t *testing.T) {
	a := &OpencodeAdapter{}
	body, err := a.EncodeRequest(ir.Request{Model: "big-pickle"}, "big-pickle", nil)
	if err != nil {
		t.Fatal(err)
	}
	tools, _ := body["tools"].([]any)
	if len(tools) != 2 {
		t.Fatalf("expected read+shell decoys, got %v", body["tools"])
	}
	if body["tool_choice"] != "none" {
		t.Fatalf("no-tool free request must pin tool_choice=none, got %v", body["tool_choice"])
	}
	fn := AsDict(AsDict(tools[0])["function"])
	params := AsDict(fn["parameters"])
	if _, ok := params["additionalProperties"]; !ok {
		t.Fatalf("decoy must carry the client schema: %v", params)
	}

	resp := []byte(`{"choices":[{"message":{"tool_calls":[
		{"id":"1","function":{"name":"read","arguments":"{}"}},
		{"id":"2","function":{"name":"write","arguments":"{}"}}]},"finish_reason":"tool_calls"}],"usage":{}}`)
	turn, err := a.DecodeResponse(200, resp)
	if err != nil {
		t.Fatal(err)
	}
	if len(turn.ToolCalls) != 1 || turn.ToolCalls[0].Name != "write" {
		t.Fatalf("injected decoy call must be dropped: %v", turn.ToolCalls)
	}
}

func TestOpencodeClientToolOfSameNameSurvives(t *testing.T) {
	a := &OpencodeAdapter{}
	req := ir.Request{
		Model: "big-pickle",
		Tools: []ir.Tool{{Name: "read", Description: "client's own reader",
			ParametersJSONSchema: map[string]any{"type": "object"}}},
	}
	body, err := a.EncodeRequest(req, "big-pickle", nil)
	if err != nil {
		t.Fatal(err)
	}
	tools, _ := body["tools"].([]any)
	if len(tools) != 2 {
		t.Fatalf("only the missing shell decoy may be added: %v", tools)
	}
	resp := []byte(`{"choices":[{"message":{"tool_calls":[
		{"id":"1","function":{"name":"read","arguments":"{}"}}]},"finish_reason":"tool_calls"}],"usage":{}}`)
	turn, err := a.DecodeResponse(200, resp)
	if err != nil {
		t.Fatal(err)
	}
	if len(turn.ToolCalls) != 1 || turn.ToolCalls[0].Name != "read" {
		t.Fatalf("client read call must survive: %v", turn.ToolCalls)
	}
}

func TestOpencodeResponsesCloakAndForceAuto(t *testing.T) {
	a := &OpencodeAdapter{}
	req := ir.Request{
		Model:      "muse-spark-1.2-contributor-free",
		ToolChoice: ir.ToolChoiceRequired{},
	}
	body, err := a.EncodeRequest(req, "muse-spark-1.2-contributor-free", nil)
	if err != nil {
		t.Fatal(err)
	}
	if body["tool_choice"] != "auto" {
		t.Fatalf("muse allowlist must force auto, got %v", body["tool_choice"])
	}
	tools, _ := body["tools"].([]any)
	if len(tools) != 2 {
		t.Fatalf("expected Responses decoys: %v", body["tools"])
	}
	first := AsDict(tools[0])
	if first["name"] != "read" || first["type"] != "function" {
		t.Fatalf("Responses decoy shape wrong: %v", first)
	}
	if _, wrapped := first["function"]; wrapped {
		t.Fatalf("Responses tools must not use the chat function wrapper: %v", first)
	}
	if _, ok := AsDict(first["parameters"])["additionalProperties"]; !ok {
		t.Fatalf("decoy schema incomplete: %v", first["parameters"])
	}

	resp := []byte(`{"output":[
		{"type":"function_call","call_id":"1","name":"read","arguments":"{}"},
		{"type":"function_call","call_id":"2","name":"lookup","arguments":"{}"}],"usage":{}}`)
	turn, err := a.DecodeResponse(200, resp)
	if err != nil {
		t.Fatal(err)
	}
	if len(turn.ToolCalls) != 1 || turn.ToolCalls[0].Name != "lookup" {
		t.Fatalf("only injected decoys are filtered: %v", turn.ToolCalls)
	}
}

func TestOpencodeCloakOptOut(t *testing.T) {
	a := &OpencodeAdapter{}
	body, err := a.EncodeRequest(
		ir.Request{Model: "muse-spark-1.3-contributor-free"},
		"muse-spark-1.3-contributor-free",
		map[string]any{"opencode_free_tier_cloak": false},
	)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := body["tools"]; ok {
		t.Fatalf("cloak opt-out must leave tools absent: %v", body["tools"])
	}
	if _, ok := body["opencode_free_tier_cloak"]; ok {
		t.Fatal("control flag must not reach the wire")
	}
}

func TestOpencodeStreamDropsDecoyCalls(t *testing.T) {
	a := &OpencodeAdapter{}
	if _, err := a.EncodeRequest(ir.Request{Model: "big-pickle"}, "big-pickle", nil); err != nil {
		t.Fatal(err)
	}
	deltas, err := a.DecodeStreamEvent("", `{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"1","function":{"name":"read","arguments":"{}"}}]}}]}`)
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range deltas {
		if open, ok := d.(streaming.ToolCallOpen); ok {
			t.Fatalf("decoy open leaked: %+v", open)
		}
	}
	deltas, err = a.DecodeStreamEvent("", `{"choices":[{"delta":{},"finish_reason":"tool_calls"}]}`)
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range deltas {
		if close, ok := d.(streaming.ToolCallClose); ok {
			t.Fatalf("decoy close leaked: %+v", close)
		}
		if finish, ok := d.(streaming.Finish); ok && finish.StopReason != ir.StopStop {
			t.Fatalf("decoy-only finish must become stop: %+v", finish)
		}
	}
}

func TestOpencodeResponsesStreamKeepsClientTool(t *testing.T) {
	a := &OpencodeAdapter{}
	if _, err := a.EncodeRequest(ir.Request{Model: "muse-spark-1.2-contributor-free"},
		"muse-spark-1.2-contributor-free", nil); err != nil {
		t.Fatal(err)
	}
	deltas, err := a.DecodeStreamEvent("", `{"type":"response.output_item.added","output_index":0,"item":{"type":"function_call","call_id":"1","name":"lookup"}}`)
	if err != nil {
		t.Fatal(err)
	}
	var opens []streaming.ToolCallOpen
	for _, d := range deltas {
		if open, ok := d.(streaming.ToolCallOpen); ok {
			opens = append(opens, open)
		}
	}
	if len(opens) != 1 || opens[0].Name != "lookup" {
		t.Fatalf("client call must survive: %+v", opens)
	}
	deltas, err = a.DecodeStreamEvent("", `{"type":"response.completed","response":{"usage":{}}}`)
	if err != nil {
		t.Fatal(err)
	}
	for _, d := range deltas {
		if finish, ok := d.(streaming.Finish); ok && finish.StopReason != ir.StopStop {
			t.Fatalf("unexpected finish: %+v", finish)
		}
	}
}
