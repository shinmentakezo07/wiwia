package ir

import "encoding/json"

// PartType identifies the concrete content-part variant.
type PartType string

const (
	PartText       PartType = "text"
	PartImage      PartType = "image"
	PartToolUse    PartType = "tool_use"
	PartToolResult PartType = "tool_result"
	PartThinking   PartType = "thinking"
	PartAudio      PartType = "audio"
	PartDocument   PartType = "document"
)

// Part is a tagged content block carried as a flexible payload map. The Type
// discriminator lets adapters/encoders switch without an interface assertion on
// a concrete Go type, which keeps dialect translation driven by data (the same
// way the Python IR uses tagged dataclasses).
type Part struct {
	Type    PartType       `json:"type"`
	Payload map[string]any `json:"-"`
}

func (p Part) MarshalJSON() ([]byte, error) {
	if p.Payload != nil {
		return json.Marshal(p.Payload)
	}
	return json.Marshal(map[string]any{"type": p.Type})
}

func (p *Part) UnmarshalJSON(data []byte) error {
	var raw map[string]any
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	if t, ok := raw["type"].(string); ok {
		p.Type = PartType(t)
	}
	p.Payload = raw
	return nil
}

func (p Part) Str(key string) string {
	if p.Payload == nil {
		return ""
	}
	s, _ := p.Payload[key].(string)
	return s
}

func (p Part) Bool(key string) bool {
	if p.Payload == nil {
		return false
	}
	b, _ := p.Payload[key].(bool)
	return b
}

func (p Part) Map(key string) map[string]any {
	if p.Payload == nil {
		return nil
	}
	m, _ := p.Payload[key].(map[string]any)
	return m
}

func NewTextPart(text string) Part {
	return Part{Type: PartText, Payload: map[string]any{"type": "text", "text": text}}
}

func NewImagePart(url, b64, mime string) Part {
	m := map[string]any{"type": "image", "mime": mime}
	if url != "" {
		m["url"] = url
	}
	if b64 != "" {
		m["b64"] = b64
	}
	return Part{Type: PartImage, Payload: m}
}

func NewToolUsePart(id, name string, args map[string]any) Part {
	return Part{Type: PartToolUse, Payload: map[string]any{
		"type": "tool_use", "id": id, "name": name, "args": args, "block_type": "tool_use",
	}}
}

func NewToolResultPart(toolUseID, content string, isError bool) Part {
	return Part{Type: PartToolResult, Payload: map[string]any{
		"type": "tool_result", "tool_use_id": toolUseID, "content": content,
		"is_error": isError, "block_type": "tool_result",
	}}
}

func NewThinkingPart(text string) Part {
	return Part{Type: PartThinking, Payload: map[string]any{
		"type": "thinking", "text": text, "block_type": "thinking",
	}}
}
