package providers

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"
	"sync/atomic"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

const geminiDefaultBase = "https://generativelanguage.googleapis.com/v1beta"

var geminiToolSeq atomic.Int64

// syntheticGeminiToolID mints an id for a functionCall, which carries none.
func syntheticGeminiToolID(name string) string {
	if name == "" {
		name = "x"
	}
	return "call_" + name + "_" + strconv.FormatInt(geminiToolSeq.Add(1), 10)
}

// GeminiAdapter speaks the generateContent REST surface (and its alt=sse
// streaming twin).
type GeminiAdapter struct {
	providerType string

	// Per-stream decode state (cleared by Reset).
	started   bool
	finished  bool
	ended     bool
	toolSeq   int
	openTools map[int]*openToolState
}

func (a *GeminiAdapter) ProviderType() string { return a.providerType }

func (a *GeminiAdapter) Headers(key ProviderKeyRef) map[string]string {
	return map[string]string{
		"x-goog-api-key": key.Secret,
		"Content-Type":   "application/json",
	}
}

func (a *GeminiAdapter) BuildURL(baseURL, modelID string, stream bool) string {
	base := strings.TrimRight(baseURL, "/")
	if base == "" {
		base = geminiDefaultBase
	}
	if stream {
		return base + "/models/" + modelID + ":streamGenerateContent?alt=sse"
	}
	return base + "/models/" + modelID + ":generateContent"
}

func (a *GeminiAdapter) Reset() {
	a.started = false
	a.finished = false
	a.ended = false
	a.toolSeq = 0
	a.openTools = nil
}

// -- request encoding --------------------------------------------------------

func (a *GeminiAdapter) EncodeRequest(req ir.Request, modelID string, params map[string]any) (map[string]any, error) {
	g := req.GenParams

	// functionResponse.name must be the function NAME, resolved from history.
	toolNames := map[string]string{}
	for _, m := range req.Messages {
		for _, p := range m.Parts {
			if p.Type == ir.PartToolUse && p.Str("id") != "" {
				toolNames[p.Str("id")] = p.Str("name")
			}
		}
	}

	var systemParts []string
	contents := make([]any, 0, len(req.Messages))
	seenNonSystem := false
	for _, m := range req.Messages {
		if m.Role == ir.RoleSystem && !seenNonSystem {
			for _, p := range m.Parts {
				if p.Type == ir.PartText && p.Str("text") != "" {
					systemParts = append(systemParts, p.Str("text"))
				}
			}
			continue
		}
		if m.Role == ir.RoleSystem {
			// contents accepts only user/model: fold a mid-conversation system
			// entry in place as marked user text.
			var parts []any
			for _, p := range m.Parts {
				if p.Type == ir.PartText && p.Str("text") != "" {
					parts = append(parts, map[string]any{"text": "[system]\n" + p.Str("text")})
				}
			}
			if len(parts) > 0 {
				contents = geminiAppendParts(contents, "user", parts)
			}
			continue
		}
		seenNonSystem = true

		role := "user"
		if m.Role == ir.RoleAssistant {
			role = "model"
		}
		var parts []any
		for _, p := range m.Parts {
			switch p.Type {
			case ir.PartText:
				parts = append(parts, map[string]any{"text": p.Str("text")})
			case ir.PartThinking:
				if p.Str("block_type") == "redacted_thinking" {
					continue
				}
				entry := map[string]any{"text": p.Str("text"), "thought": true}
				if sig := p.Str("signature"); sig != "" {
					entry["thoughtSignature"] = sig
				}
				parts = append(parts, entry)
			case ir.PartImage, ir.PartDocument:
				if p.Str("file_id") != "" {
					continue
				}
				if b64 := p.Str("b64"); b64 != "" {
					mime := p.Str("mime")
					if mime == "" {
						mime = "application/octet-stream"
					}
					parts = append(parts, map[string]any{"inline_data": map[string]any{"mime_type": mime, "data": b64}})
				} else if url := p.Str("url"); url != "" {
					parts = append(parts, map[string]any{"file_data": map[string]any{"file_uri": url}})
				}
			case ir.PartToolUse:
				if p.Str("builtin") != "" {
					continue
				}
				args := p.Map("args")
				if args == nil {
					args = map[string]any{}
				}
				parts = append(parts, map[string]any{"functionCall": map[string]any{"name": p.Str("name"), "args": args}})
			case ir.PartToolResult:
				if bt := p.Str("block_type"); bt != "" && bt != "tool_result" {
					parts = append(parts, map[string]any{"text": p.Str("content")})
					continue
				}
				response := map[string]any{"result": p.Str("content")}
				var parsed map[string]any
				if err := json.Unmarshal([]byte(p.Str("content")), &parsed); err == nil && parsed != nil {
					response = parsed
				}
				name := toolNames[p.Str("tool_use_id")]
				if name == "" {
					name = strings.TrimPrefix(p.Str("tool_use_id"), "call_")
				}
				parts = append(parts, map[string]any{"functionResponse": map[string]any{"name": name, "response": response}})
			}
		}
		if len(parts) > 0 {
			contents = geminiAppendParts(contents, role, parts)
		}
	}

	body := map[string]any{"contents": contents}
	if len(systemParts) > 0 {
		body["systemInstruction"] = map[string]any{
			"parts": []any{map[string]any{"text": strings.Join(systemParts, "\n")}},
		}
	}

	gen := map[string]any{}
	if g.MaxTokens != nil {
		gen["maxOutputTokens"] = *g.MaxTokens
	} else if v, ok := asIntValue(params["max_tokens"]); ok {
		gen["maxOutputTokens"] = v
	}
	if g.Temperature != nil {
		gen["temperature"] = *g.Temperature
	}
	if g.TopP != nil {
		gen["topP"] = *g.TopP
	}
	if g.TopK != nil {
		gen["topK"] = *g.TopK
	}
	if len(g.Stop) > 0 {
		gen["stopSequences"] = g.Stop
	}
	if rf := g.ResponseFormat; rf != nil {
		switch rf.Type {
		case "json_object":
			gen["responseMimeType"] = "application/json"
		case "json_schema":
			gen["responseMimeType"] = "application/json"
			if len(rf.JSONSchema) > 0 {
				schema := map[string]any{}
				for k, v := range rf.JSONSchema {
					schema[k] = v
				}
				if rf.Name != nil && *rf.Name != "" {
					schema["title"] = *rf.Name
				}
				gen["responseSchema"] = schema
			}
		}
	}
	if g.ThinkingBudget != nil && *g.ThinkingBudget == 0 {
		gen["thinkingConfig"] = map[string]any{"thinkingBudget": 0}
	} else if effort := g.EffectiveReasoningEffort(); effort == "none" {
		gen["thinkingConfig"] = map[string]any{"thinkingBudget": 0}
	} else if budget, ok := g.EffectiveThinkingBudget(); ok {
		gen["thinkingConfig"] = map[string]any{"thinkingBudget": budget}
	}
	if len(gen) > 0 {
		body["generationConfig"] = gen
	}

	if decls := encodeGeminiTools(req.Tools); len(decls) > 0 {
		body["tools"] = []any{map[string]any{"functionDeclarations": decls}}
		if fcc := encodeGeminiToolChoice(req.ToolChoice); fcc != nil {
			body["toolConfig"] = map[string]any{"functionCallingConfig": fcc}
		}
	}

	mergeDefaults(body, req.Extras)
	return body, nil
}

// geminiAppendParts merges adjacent same-role contents, as the API expects.
func geminiAppendParts(contents []any, role string, parts []any) []any {
	if len(contents) > 0 {
		last := AsDict(contents[len(contents)-1])
		if mapStr(last, "role") == role {
			last["parts"] = append(AsList(last["parts"]), parts...)
			return contents
		}
	}
	return append(contents, map[string]any{"role": role, "parts": parts})
}

func encodeGeminiTools(tools []ir.Tool) []any {
	var out []any
	for _, t := range tools {
		if t.Builtin != nil {
			if *t.Builtin == "web_search" {
				out = append(out, map[string]any{"google_search": map[string]any{}})
			} else if raw := ir.PassthroughWireType("gemini", t.BuiltinConfig); raw != "" {
				out = append(out, map[string]any{raw: map[string]any{}})
			}
			continue
		}
		schema := t.ParametersJSONSchema
		if schema == nil {
			schema = map[string]any{"type": "object", "properties": map[string]any{}}
		}
		desc := t.Description
		if len(t.InputExamples) > 0 {
			if raw, err := json.Marshal(t.InputExamples); err == nil {
				if desc != "" {
					desc = desc + "\n\nExample inputs:\n" + string(raw)
				} else {
					desc = "Example inputs:\n" + string(raw)
				}
			}
		}
		out = append(out, map[string]any{
			"name": t.Name, "description": desc, "parameters": schema,
		})
	}
	return out
}

func encodeGeminiToolChoice(tc ir.ToolChoice) map[string]any {
	switch c := tc.(type) {
	case ir.ToolChoiceNone, *ir.ToolChoiceNone:
		return map[string]any{"mode": "NONE"}
	case ir.ToolChoiceAuto, *ir.ToolChoiceAuto:
		return map[string]any{"mode": "AUTO"}
	case ir.ToolChoiceRequired, *ir.ToolChoiceRequired:
		return map[string]any{"mode": "ANY"}
	case ir.ToolChoiceNamed:
		return map[string]any{"mode": "ANY", "allowedFunctionNames": []string{c.Name}}
	case *ir.ToolChoiceNamed:
		return map[string]any{"mode": "ANY", "allowedFunctionNames": []string{c.Name}}
	}
	return nil
}

// -- response decoding -------------------------------------------------------

func (a *GeminiAdapter) DecodeResponse(status int, body []byte) (ir.AssistantTurn, error) {
	var turn ir.AssistantTurn
	if status >= 400 {
		return turn, ErrorFromProviderStatus(status, body, a.providerType)
	}
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		return turn, &WiwiError{
			Status: 502, EType: "api_error", Retryable: true,
			Message: fmt.Sprintf("%s returned unparseable JSON: %v", a.providerType, err),
		}
	}
	pf := AsDict(data["promptFeedback"])
	blocked := mapStr(pf, "blockReason") != ""
	candidates := AsList(data["candidates"])
	var cand map[string]any
	if len(candidates) > 0 {
		cand = AsDict(candidates[0])
	}
	content := AsDict(cand["content"])
	for _, raw := range AsList(content["parts"]) {
		part, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		if thought, _ := part["thought"].(bool); thought {
			turn.Thinking = append(turn.Thinking, ir.ThinkingPart{
				Text: mapStr(part, "text"), Signature: mapStr(part, "thoughtSignature"),
				BlockType: "thinking",
			})
			continue
		}
		if text, ok := part["text"].(string); ok {
			turn.Text += text
			continue
		}
		if fc, ok := part["functionCall"].(map[string]any); ok {
			name := mapStr(fc, "name")
			turn.ToolCalls = append(turn.ToolCalls, ir.ToolUsePart{
				ID: syntheticGeminiToolID(name), Name: name,
				Args: AsDict(fc["args"]), BlockType: "tool_use",
			})
		}
	}
	switch {
	case blocked:
		turn.StopReason = ir.StopContentFilter
	case len(turn.ToolCalls) > 0:
		turn.StopReason = ir.StopToolCall
	default:
		turn.StopReason = geminiStopReason(cand["finishReason"])
	}
	u := AsDict(data["usageMetadata"])
	cachedInPrompt := true
	thoughts := mapInt(u, "thoughtsTokenCount")
	turn.Usage = ir.Usage{
		PromptTokens:     mapInt(u, "promptTokenCount"),
		CompletionTokens: mapInt(u, "candidatesTokenCount") + thoughts,
		CachedTokens:     mapInt(u, "cachedContentTokenCount"),
		ReasoningTokens:  thoughts,
		CachedInPrompt:   &cachedInPrompt,
	}
	return turn, nil
}

// -- stream decoding ---------------------------------------------------------

func (a *GeminiAdapter) DecodeStreamEvent(event, data string) ([]streaming.Delta, error) {
	var out []streaming.Delta
	if strings.TrimSpace(data) == "[DONE]" {
		a.ensureStarted(&out, "")
		closed := a.closeAllTools(&out)
		if closed && !a.finished {
			out = append(out, streaming.Finish{StopReason: ir.StopToolCall})
			a.finished = true
		}
		if !a.ended {
			out = append(out, streaming.StreamEnd{})
			a.ended = true
		}
		return out, nil
	}

	var payload map[string]any
	if err := json.Unmarshal([]byte(data), &payload); err != nil {
		return nil, nil
	}
	a.ensureStarted(&out, mapStr(payload, "modelVersion"))

	if errObj, ok := payload["error"].(map[string]any); ok {
		a.closeAllTools(&out)
		status := 0
		if n, ok := asIntValue(errObj["code"]); ok {
			status = n
		}
		out = append(out, streaming.StreamError{
			Message: mapStr(errObj, "message"), Kind: "status", Status: status,
		})
		a.ended = true
		return out, nil
	}

	if pf := AsDict(payload["promptFeedback"]); mapStr(pf, "blockReason") != "" {
		a.closeAllTools(&out)
		if !a.finished {
			out = append(out, streaming.Finish{StopReason: ir.StopContentFilter})
			a.finished = true
		}
		if !a.ended {
			out = append(out, streaming.StreamEnd{})
			a.ended = true
		}
		return out, nil
	}

	candidates := AsList(payload["candidates"])
	var cand map[string]any
	if len(candidates) > 0 {
		cand = AsDict(candidates[0])
	}
	content := AsDict(cand["content"])
	for _, raw := range AsList(content["parts"]) {
		part, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		if thought, _ := part["thought"].(bool); thought {
			out = append(out, streaming.ThinkingDelta{
				Text: mapStr(part, "text"), Signature: mapStr(part, "thoughtSignature"),
				BlockType: "thinking",
			})
			continue
		}
		if text, ok := part["text"].(string); ok {
			if text != "" {
				out = append(out, streaming.TextDelta{Text: text})
			}
			continue
		}
		if fc, ok := part["functionCall"].(map[string]any); ok {
			name := mapStr(fc, "name")
			idx := a.toolSeq
			a.toolSeq++
			if a.openTools == nil {
				a.openTools = map[int]*openToolState{}
			}
			a.openTools[idx] = &openToolState{ID: syntheticGeminiToolID(name), Name: name}
			out = append(out, streaming.ToolCallOpen{
				Index: idx, ID: a.openTools[idx].ID, Name: name, BlockType: "tool_use",
			})
			if args := AsDict(fc["args"]); len(args) > 0 {
				if raw, err := json.Marshal(args); err == nil {
					out = append(out, streaming.ToolCallArgsDelta{Index: idx, ArgsFragment: string(raw)})
				}
			}
			delete(a.openTools, idx)
			out = append(out, streaming.ToolCallClose{Index: idx})
		}
	}

	if u, ok := payload["usageMetadata"].(map[string]any); ok {
		thoughts := mapInt(u, "thoughtsTokenCount")
		cachedInPrompt := true
		out = append(out, streaming.UsageFinal{
			Prompt:         mapInt(u, "promptTokenCount"),
			Cached:         mapInt(u, "cachedContentTokenCount"),
			Reasoning:      thoughts,
			Output:         mapInt(u, "candidatesTokenCount") + thoughts,
			CachedInPrompt: &cachedInPrompt,
		})
	}

	if fr, ok := cand["finishReason"]; ok && fr != nil {
		if s, _ := fr.(string); s != "" && !a.finished {
			a.closeAllTools(&out)
			out = append(out, streaming.Finish{StopReason: geminiStopReason(fr)})
			a.finished = true
			if !a.ended {
				out = append(out, streaming.StreamEnd{})
				a.ended = true
			}
		}
	}
	return out, nil
}

func (a *GeminiAdapter) ensureStarted(out *[]streaming.Delta, model string) {
	if a.started {
		return
	}
	a.started = true
	*out = append(*out, streaming.StreamStart{Model: model})
}

func (a *GeminiAdapter) closeAllTools(out *[]streaming.Delta) bool {
	if len(a.openTools) == 0 {
		return false
	}
	for _, idx := range sortedToolIndices(a.openTools) {
		*out = append(*out, streaming.ToolCallClose{Index: idx})
	}
	a.openTools = nil
	return true
}

func init() {
	Register("gemini", func() Adapter { return &GeminiAdapter{providerType: "gemini"} })
}
