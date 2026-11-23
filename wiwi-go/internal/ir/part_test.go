package ir

import (
	"encoding/json"
	"testing"
)

func TestNewAudioPart(t *testing.T) {
	t.Run("with_explicit_mime", func(t *testing.T) {
		p := NewAudioPart("base64data", "audio/mp3")
		if p.Type != PartAudio {
			t.Errorf("expected type %q, got %q", PartAudio, p.Type)
		}
		if p.Str("b64") != "base64data" {
			t.Errorf("expected b64 %q, got %q", "base64data", p.Str("b64"))
		}
		if p.Str("mime") != "audio/mp3" {
			t.Errorf("expected mime %q, got %q", "audio/mp3", p.Str("mime"))
		}
	})

	t.Run("default_mime", func(t *testing.T) {
		p := NewAudioPart("data", "")
		if p.Str("mime") != "audio/wav" {
			t.Errorf("expected default mime %q, got %q", "audio/wav", p.Str("mime"))
		}
	})
}

func TestNewDocumentPart(t *testing.T) {
	t.Run("with_b64", func(t *testing.T) {
		p := NewDocumentPart("base64pdf", "", "application/pdf")
		if p.Type != PartDocument {
			t.Errorf("expected type %q, got %q", PartDocument, p.Type)
		}
		if p.Str("b64") != "base64pdf" {
			t.Errorf("expected b64 %q, got %q", "base64pdf", p.Str("b64"))
		}
		if p.Str("url") != "" {
			t.Errorf("expected empty url, got %q", p.Str("url"))
		}
	})

	t.Run("with_url", func(t *testing.T) {
		p := NewDocumentPart("", "https://example.com/doc.pdf", "")
		if p.Str("url") != "https://example.com/doc.pdf" {
			t.Errorf("expected url, got %q", p.Str("url"))
		}
		if p.Str("mime") != "application/pdf" {
			t.Errorf("expected default mime, got %q", p.Str("mime"))
		}
	})

	t.Run("with_both", func(t *testing.T) {
		p := NewDocumentPart("data", "https://example.com/doc", "text/plain")
		if p.Str("b64") != "data" {
			t.Errorf("expected b64")
		}
		if p.Str("url") != "https://example.com/doc" {
			t.Errorf("expected url")
		}
		if p.Str("mime") != "text/plain" {
			t.Errorf("expected custom mime")
		}
	})
}

func TestNewDocumentPartWithFileID(t *testing.T) {
	p := NewDocumentPartWithFileID("file-123")
	if p.Type != PartDocument {
		t.Errorf("expected type %q, got %q", PartDocument, p.Type)
	}
	if p.Str("file_id") != "file-123" {
		t.Errorf("expected file_id %q, got %q", "file-123", p.Str("file_id"))
	}
}

func TestSetDocumentMetadata(t *testing.T) {
	p := NewDocumentPart("data", "", "application/pdf")
	cc := map[string]any{"type": "ephemeral"}
	p = SetDocumentMetadata(p, "Report", "Q4 results", cc)

	if p.Str("name") != "Report" {
		t.Errorf("expected name %q, got %q", "Report", p.Str("name"))
	}
	if p.Str("context") != "Q4 results" {
		t.Errorf("expected context %q, got %q", "Q4 results", p.Str("context"))
	}
	if p.Map("cache_control")["type"] != "ephemeral" {
		t.Errorf("expected cache_control")
	}
}

func TestPartIntAccessor(t *testing.T) {
	p := Part{
		Type: PartText,
		Payload: map[string]any{
			"count": 42,
			"float": 3.14,
			"str":   "not a number",
		},
	}

	if got := p.Int("count"); got != 42 {
		t.Errorf("expected 42, got %d", got)
	}
	if got := p.Int("float"); got != 3 {
		t.Errorf("expected 3 (truncated), got %d", got)
	}
	if got := p.Int("str"); got != 0 {
		t.Errorf("expected 0 for non-numeric, got %d", got)
	}
	if got := p.Int("missing"); got != 0 {
		t.Errorf("expected 0 for missing key, got %d", got)
	}
}

func TestPartSliceAccessor(t *testing.T) {
	p := Part{
		Type: PartToolResult,
		Payload: map[string]any{
			"images": []any{
				map[string]any{"url": "http://example.com/1.png"},
				map[string]any{"url": "http://example.com/2.png"},
			},
			"extra_blocks": []any{
				map[string]any{"type": "tool_reference", "id": "t1"},
			},
		},
	}

	images := p.Slice("images")
	if len(images) != 2 {
		t.Errorf("expected 2 images, got %d", len(images))
	}

	extra := p.Slice("extra_blocks")
	if len(extra) != 1 {
		t.Errorf("expected 1 extra_block, got %d", len(extra))
	}

	if p.Slice("missing") != nil {
		t.Errorf("expected nil for missing key")
	}
}

func TestPartJSONRoundTrip(t *testing.T) {
	original := NewDocumentPart("base64data", "", "application/pdf")
	original = SetDocumentMetadata(original, "Test Doc", "Context info", map[string]any{"type": "ephemeral"})

	data, err := json.Marshal(original)
	if err != nil {
		t.Fatalf("marshal failed: %v", err)
	}

	var decoded Part
	if err := json.Unmarshal(data, &decoded); err != nil {
		t.Fatalf("unmarshal failed: %v", err)
	}

	if decoded.Type != PartDocument {
		t.Errorf("expected type %q, got %q", PartDocument, decoded.Type)
	}
	if decoded.Str("b64") != "base64data" {
		t.Errorf("expected b64 preserved")
	}
	if decoded.Str("name") != "Test Doc" {
		t.Errorf("expected name preserved")
	}
	if decoded.Str("context") != "Context info" {
		t.Errorf("expected context preserved")
	}
}
