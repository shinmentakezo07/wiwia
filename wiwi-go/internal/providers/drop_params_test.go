package providers

import (
	"testing"

	"wiwi-go/internal/ir"
)

func baseRequest(extras map[string]any) ir.Request {
	return ir.Request{
		Model:    "m",
		Messages: []ir.Message{{Role: ir.RoleUser, Parts: []ir.Part{ir.NewTextPart("hi")}}},
		Extras:   extras,
	}
}

func TestOpenAIExtrasHonourDropParams(t *testing.T) {
	a := &OpenAIAdapter{providerType: "openai"}
	req := baseRequest(map[string]any{"user": "u1", "bogus_param": 1})

	body, err := a.EncodeRequest(req, "m", map[string]any{"drop_params": true})
	if err != nil {
		t.Fatal(err)
	}
	if body["user"] != "u1" {
		t.Fatalf("standard extra was dropped: %v", body["user"])
	}
	if _, ok := body["bogus_param"]; ok {
		t.Fatal("unknown extra must be dropped when drop_params is true")
	}

	body, err = a.EncodeRequest(req, "m", map[string]any{"drop_params": false})
	if err != nil {
		t.Fatal(err)
	}
	if body["bogus_param"] != 1 {
		t.Fatal("unknown extra must ride through when drop_params is false")
	}
}

func TestAnthropicExtrasHonourDropParams(t *testing.T) {
	a := &AnthropicAdapter{}
	req := baseRequest(map[string]any{"metadata": map[string]any{"user_id": "u"}, "bogus": true})

	body, err := a.EncodeRequest(req, "m", map[string]any{"drop_params": true})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := body["metadata"]; !ok {
		t.Fatal("standard Anthropic extra was dropped")
	}
	if _, ok := body["bogus"]; ok {
		t.Fatal("unknown extra must be dropped when drop_params is true")
	}

	body, err = a.EncodeRequest(req, "m", map[string]any{"drop_params": false})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := body["bogus"]; !ok {
		t.Fatal("unknown extra must ride through when drop_params is false")
	}
}

func TestAnthropicMCPAndContextManagementAreModelled(t *testing.T) {
	a := &AnthropicAdapter{}
	req := baseRequest(nil)
	req.MCPServers = []ir.MCPServer{{
		Type: "url", Name: "docs", URL: "https://example.test/mcp",
		AuthorizationToken: "tok", ToolConfiguration: map[string]any{"enabled": true},
	}}
	req.Container = map[string]any{"id": "c1"}
	req.ContextManagement = map[string]any{"edits": []any{}}

	body, err := a.EncodeRequest(req, "m", map[string]any{"drop_params": true})
	if err != nil {
		t.Fatal(err)
	}
	servers, ok := body["mcp_servers"].([]ir.MCPServer)
	if !ok || len(servers) != 1 || servers[0].URL != "https://example.test/mcp" {
		t.Fatalf("mcp_servers not forwarded: %#v", body["mcp_servers"])
	}
	if _, ok := body["container"]; !ok {
		t.Fatal("container not forwarded")
	}
	if _, ok := body["context_management"]; !ok {
		t.Fatal("context_management not forwarded")
	}
}

func TestGeminiDropsUnknownExtras(t *testing.T) {
	a := &GeminiAdapter{}
	req := baseRequest(map[string]any{"bogus": 1})
	body, err := a.EncodeRequest(req, "m", map[string]any{"drop_params": true})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := body["bogus"]; ok {
		t.Fatal("gemini must not forward unknown extras")
	}
	body, err = a.EncodeRequest(req, "m", map[string]any{"drop_params": false})
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := body["bogus"]; !ok {
		t.Fatal("gemini should forward extras when drop_params is false")
	}
}
