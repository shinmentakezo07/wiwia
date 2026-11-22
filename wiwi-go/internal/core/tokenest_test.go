package core

import (
	"testing"

	"wiwi-go/internal/ir"
)

func estRequest(parts ...ir.Part) ir.Request {
	return ir.Request{
		Model:    "g",
		Messages: []ir.Message{{Role: ir.RoleUser, Parts: parts}},
	}
}

// TestEstimateRequestTokensCountsToolsAndText: the estimate must include the
// system prompt, tool definitions and the message text, so TPM admission and
// the usage fallback are the right order of magnitude.
func TestEstimateRequestTokensCountsToolsAndText(t *testing.T) {
	req := estRequest(ir.NewTextPart("write me a haiku about sliding windows please"))
	base := EstimateRequestTokens(req)
	if base <= 0 {
		t.Fatalf("a non-empty request must estimate > 0, got %d", base)
	}
	withTools := req
	withTools.Tools = []ir.Tool{{
		Name:        "lookup",
		Description: "look something up in the catalogue",
		ParametersJSONSchema: map[string]any{
			"type": "object",
			"properties": map[string]any{
				"query": map[string]any{"type": "string", "description": "search terms"},
			},
		},
	}}
	if EstimateRequestTokens(withTools) <= base {
		t.Fatal("tool schemas must contribute to the estimate")
	}
	if EstimateRequestTokens(estRequest()) != 0 {
		t.Fatal("an empty request must estimate 0")
	}
}

// TestMediaTokensNotFree: inline base64 media is billed by decoded size; the
// old behaviour counted only url/file_id and made a screenshot free.
func TestMediaTokensNotFree(t *testing.T) {
	b64 := make([]byte, 300*1024)
	for i := range b64 {
		b64[i] = 'A'
	}
	img := ir.Part{Type: ir.PartImage, Payload: map[string]any{
		"type": "image", "mime": "image/png", "b64": string(b64),
	}}
	withMedia := EstimateRequestTokens(estRequest(img))
	textOnly := EstimateRequestTokens(estRequest(ir.NewTextPart("describe this")))
	if withMedia <= textOnly {
		t.Fatalf("media must add tokens: %d vs %d", withMedia, textOnly)
	}
	if got := EstimateMediaTokens(300*1024, "image/png"); got < 500 {
		t.Fatalf("300KB png should be ~1000+ tokens, got %d", got)
	}
	// A JPEG packs more pixels into the same bytes, so the same byte count
	// implies a larger image and therefore MORE tokens. This direction matters:
	// inverting it would under-bill exactly the format phones produce most.
	if EstimateMediaTokens(300*1024, "image/jpeg") <= EstimateMediaTokens(300*1024, "image/png") {
		t.Fatal("jpeg should cost more than png for the same byte count")
	}
	if EstimateMediaTokens(0, "image/png") != 0 {
		t.Fatal("no payload, no tokens")
	}
	if got := EstimateMediaTokens(9000, "application/pdf"); got < 1500 {
		t.Fatalf("pdf should bill at least one page, got %d", got)
	}
}

// TestFlattenRequestTextIsTotal: a sloppy client must not be able to panic the
// estimator — it runs inside the stream pump, so a panic there kills the stream
// with no terminal frame (Python AUDIT #211).
func TestFlattenRequestTextIsTotal(t *testing.T) {
	req := estRequest(
		ir.NewTextPart("hi"),
		ir.Part{Type: ir.PartImage, Payload: map[string]any{"url": nil, "mime": 42}},
		ir.Part{Type: ir.PartToolResult, Payload: map[string]any{"content": 7, "images": []any{"x", nil}}},
		ir.Part{Type: ir.PartToolUse, Payload: map[string]any{"name": nil, "args": map[string]any{"a": 1}}},
	)
	if got := FlattenRequestText(req); got == "" {
		t.Fatal("expected some flattened text")
	}
}
