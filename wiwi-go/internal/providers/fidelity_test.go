package providers

import (
	"encoding/json"
	"testing"

	"wiwi-go/internal/ir"
)

func TestOpenRouterReasoning(t *testing.T) {
	effort := "high"
	g := ir.GenParams{ReasoningEffort: &effort}
	body := map[string]any{"max_tokens": 100}
	applyOpenRouterReasoning(body, g)
	if _, ok := body["reasoning_effort"]; ok {
		t.Fatal("reasoning_effort must be removed")
	}
	obj, ok := body["reasoning"].(map[string]any)
	if !ok || obj["effort"] != "high" {
		t.Fatalf("reasoning: %v", body["reasoning"])
	}
}

func TestOpenRouterReasoningBudget(t *testing.T) {
	budget := 8000
	g := ir.GenParams{ThinkingBudget: &budget}
	body := map[string]any{"max_tokens": 100}
	applyOpenRouterReasoning(body, g)
	obj := body["reasoning"].(map[string]any)
	if obj["max_tokens"] != 8000 {
		t.Fatalf("budget: %v", obj)
	}
	if body["max_tokens"].(int) <= 8000 {
		t.Fatalf("max_tokens must be lifted above the budget, got %v", body["max_tokens"])
	}
}

func TestNIMSanitize(t *testing.T) {
	tools := []any{map[string]any{"type": "function", "function": map[string]any{
		"name": "f",
		"parameters": map[string]any{
			"type": "object",
			"properties": map[string]any{
				"type": map[string]any{"type": "string"},
				"x":    map[string]any{"type": "string"},
			},
			"additionalProperties": true,
		},
	}}}
	clean, aliases := sanitizeNIMTools(tools)
	fn := clean[0].(map[string]any)["function"].(map[string]any)
	params := fn["parameters"].(map[string]any)
	if _, ok := params["additionalProperties"]; ok {
		t.Fatal("boolean subschema must be stripped")
	}
	props := params["properties"].(map[string]any)
	if _, ok := props["_nim_arg_type"]; !ok {
		t.Fatalf("type param must be aliased: %v", props)
	}
	reversed := reverseNIMArgs("f", map[string]any{"_nim_arg_type": "x"}, aliases)
	if reversed["type"] != "x" {
		t.Fatalf("reverse: %v", reversed)
	}
}

func TestWorkBuddyBusinessError(t *testing.T) {
	body, _ := json.Marshal(map[string]any{"error": map[string]any{"message": "nope"}})
	we := workbuddyBusinessError(body)
	if we == nil || !we.Retryable {
		t.Fatalf("expected retryable business error, got %v", we)
	}
	ok, _ := json.Marshal(map[string]any{"choices": []any{}})
	if workbuddyBusinessError(ok) != nil {
		t.Fatal("a normal completion must not be an error")
	}
}

func TestAnthropicOutputConfig(t *testing.T) {
	effort := "max"
	g := ir.GenParams{Effort: &effort}
	oc := anthropicOutputConfig(g)
	if oc == nil || oc["effort"] != "max" {
		t.Fatalf("output_config: %v", oc)
	}
}
