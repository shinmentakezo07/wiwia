package wire

import "testing"

func TestMessagesDecodesMCPAndContextManagement(t *testing.T) {
	req := decodeOK(t, SurfaceMessages, `{
		"model": "claude-x",
		"max_tokens": 64,
		"messages": [{"role": "user", "content": "hi"}],
		"mcp_servers": [
			{"type": "url", "name": "docs", "url": "https://example.test/mcp",
			 "authorization_token": "tok", "tool_configuration": {"enabled": true}}
		],
		"container": {"id": "c1"},
		"context_management": {"edits": []},
		"metadata": {"user_id": "u1"}
	}`)

	if len(req.MCPServers) != 1 {
		t.Fatalf("mcp_servers = %#v", req.MCPServers)
	}
	srv := req.MCPServers[0]
	if srv.Type != "url" || srv.Name != "docs" || srv.URL != "https://example.test/mcp" ||
		srv.AuthorizationToken != "tok" || srv.ToolConfiguration["enabled"] != true {
		t.Fatalf("mcp server fields lost: %#v", srv)
	}
	if req.Container["id"] != "c1" {
		t.Fatalf("container = %#v", req.Container)
	}
	if _, ok := req.ContextManagement["edits"]; !ok {
		t.Fatalf("context_management = %#v", req.ContextManagement)
	}
	// Modelled keys must not also linger in Extras, where a non-Anthropic
	// adapter could forward them to the wrong upstream.
	for _, k := range []string{"mcp_servers", "container", "context_management"} {
		if _, ok := req.Extras[k]; ok {
			t.Fatalf("%s should not be in Extras", k)
		}
	}
	if req.Extras["metadata"] == nil {
		t.Fatal("metadata should remain in Extras")
	}
}

func TestMessagesMalformedMCPServerIsSkipped(t *testing.T) {
	req := decodeOK(t, SurfaceMessages, `{
		"model": "claude-x", "max_tokens": 8,
		"messages": [{"role": "user", "content": "hi"}],
		"mcp_servers": ["garbage", {"type": "url", "url": "https://ok.test"}]
	}`)
	if len(req.MCPServers) != 1 || req.MCPServers[0].URL != "https://ok.test" {
		t.Fatalf("expected one valid server, got %#v", req.MCPServers)
	}
}
