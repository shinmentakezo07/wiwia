package core

import (
	"encoding/json"
	"strings"

	"wiwi-go/internal/ir"
)

// EstimateTokens is Python's chars/4 fallback (cost/pricing.py:estimate_tokens)
// for the local prompt estimate. tiktoken is not vendored in the Go build, so
// this is the heuristic branch — the same one Python takes when tiktoken is
// absent or the model is not in its encoding map.
func EstimateTokens(text string) int {
	if text == "" {
		return 0
	}
	if n := len(text) / 4; n > 0 {
		return n
	}
	return 1
}

// EstimateMediaTokens mirrors cost/pricing.py:estimate_media_tokens. Base64
// media is billed by decoded size, so counting only url/file_id (the old
// behaviour) made every inline image free.
func EstimateMediaTokens(payloadBytes int, mime string) int {
	if payloadBytes <= 0 {
		return 0
	}
	mime = strings.ToLower(mime)
	raw := payloadBytes * 3 / 4
	if raw < 1 {
		raw = 1
	}
	if strings.Contains(mime, "pdf") {
		pages := raw / 3500
		if pages < 1 {
			pages = 1
		}
		return pages * 1500
	}
	bpp := 0.10
	if strings.Contains(mime, "png") {
		bpp = 0.35
	}
	tokens := int(float64(raw) / 750 / bpp)
	if tokens < 1 {
		return 1
	}
	return tokens
}

// FlattenRequestText mirrors core/gateway.py:flatten_request_text: every piece
// of text that contributes to the prompt, joined with single spaces. Values are
// coerced rather than dereferenced blindly — this runs inside the stream pump,
// so an estimator that panics kills the stream with no terminal frame (Python
// AUDIT #211).
func FlattenRequestText(req ir.Request) string {
	out := make([]string, 0, len(req.Messages)*2)
	add := func(v string) {
		if v != "" {
			out = append(out, v)
		}
	}
	for _, m := range req.Messages {
		for _, p := range m.Parts {
			switch p.Type {
			case ir.PartText, ir.PartThinking:
				add(p.Str("text"))
			case ir.PartToolResult:
				add(p.Str("content"))
				for _, img := range partImages(p) {
					add(firstNonEmpty(strOf(img["url"]), strOf(img["file_id"])))
				}
			case ir.PartToolUse:
				add(p.Str("name"))
				if raw := p.Str("raw_args"); raw != "" {
					add(raw)
				} else if p.Payload != nil {
					if b, err := json.Marshal(p.Payload["args"]); err == nil {
						add(string(b))
					}
				}
			case ir.PartImage:
				add(firstNonEmpty(p.Str("url"), p.Str("file_id")))
			case ir.PartDocument:
				add(firstNonEmpty(p.Str("url"), p.Str("name")))
				add(p.Str("context"))
			}
		}
	}
	for _, t := range req.Tools {
		add(t.Name)
		add(t.Description)
		if t.ParametersJSONSchema != nil {
			if b, err := json.Marshal(t.ParametersJSONSchema); err == nil {
				add(string(b))
			}
		}
	}
	return strings.Join(out, " ")
}

func strOf(v any) string {
	s, _ := v.(string)
	return s
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if v != "" {
			return v
		}
	}
	return ""
}

// partImages reads the image payloads a tool result carries (wire codecs store
// them under "images" as raw maps).
func partImages(p ir.Part) []map[string]any {
	if p.Payload == nil {
		return nil
	}
	raw, ok := p.Payload["images"].([]map[string]any)
	if ok {
		return raw
	}
	list, ok := p.Payload["images"].([]any)
	if !ok {
		return nil
	}
	out := make([]map[string]any, 0, len(list))
	for _, v := range list {
		if m, ok := v.(map[string]any); ok {
			out = append(out, m)
		}
	}
	return out
}

// EstimateRequestTokens is the single entry point for prompt-token estimation,
// used by admission (so RPM/TPM reservations carry a real estimate) and by the
// stream pump's usage fallback. Text plus binary media.
func EstimateRequestTokens(req ir.Request) int {
	return EstimateTokens(FlattenRequestText(req)) + MediaTokens(req)
}

// MediaTokens is the prompt-token cost of binary media in the request.
func MediaTokens(req ir.Request) int {
	total := 0
	for _, m := range req.Messages {
		for _, p := range m.Parts {
			switch p.Type {
			case ir.PartImage, ir.PartDocument:
				if b64 := p.Str("b64"); b64 != "" {
					total += EstimateMediaTokens(len(b64), p.Str("mime"))
				}
			case ir.PartToolResult:
				for _, img := range partImages(p) {
					if b64 := strOf(img["b64"]); b64 != "" {
						total += EstimateMediaTokens(len(b64), strOf(img["mime"]))
					}
				}
			}
		}
	}
	return total
}
