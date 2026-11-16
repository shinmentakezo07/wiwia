package ir

import "testing"

func TestBuiltinMappings(t *testing.T) {
	if got := CanonicalFor("anthropic", "web_search_20250305"); got != "web_search" {
		t.Fatalf("canonical: %q", got)
	}
	// versioned family decodes without a registry change
	if got := CanonicalFor("anthropic", "web_search_20270101"); got != "web_search" {
		t.Fatalf("family: %q", got)
	}
	if got := CanonicalFor("openai_responses", "web_search_preview"); got != "web_search" {
		t.Fatalf("alias: %q", got)
	}
	if wt, ok := WireTypeFor("gemini", "web_search"); !ok || wt != "google_search" {
		t.Fatalf("gemini wire: %q %v", wt, ok)
	}
	if _, ok := WireTypeFor("openai_chat", "web_search"); ok {
		t.Fatal("chat must have no web_search representation")
	}
	if !IsBuiltinName("web_search") || IsBuiltinName("my_fn") {
		t.Fatal("IsBuiltinName wrong")
	}
}

func TestPassthrough(t *testing.T) {
	cfg := map[string]any{WireSurfaceKey: "anthropic", WireTypeKey: "code_execution_20250522"}
	if got := PassthroughWireType("anthropic", cfg); got != "code_execution_20250522" {
		t.Fatalf("passthrough: %q", got)
	}
	if got := PassthroughWireType("gemini", cfg); got != "" {
		t.Fatalf("cross-surface must drop, got %q", got)
	}
}
