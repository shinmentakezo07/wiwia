package wire

import (
	"fmt"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

var completionsKnownKeys = map[string]struct{}{
	"model": {}, "prompt": {}, "suffix": {}, "max_tokens": {},
	"max_completion_tokens": {}, "temperature": {}, "top_p": {}, "stop": {},
	"seed": {}, "n": {}, "stream": {}, "stream_options": {},
}

// decodeCompletions decodes the legacy `/v1/completions` body into one user
// turn.
func decodeCompletions(body map[string]any) (ir.Request, error) {
	model, _ := body["model"].(string)
	if model == "" {
		return ir.Request{}, fmt.Errorf("'model' is required")
	}
	if n, ok := coerceInt(body["n"]); ok && n != 1 {
		return ir.Request{}, fmt.Errorf("'n' must be 1 (multiple choices unsupported)")
	}
	for _, key := range []string{"logprobs", "echo"} {
		if v, ok := body[key].(bool); ok && v {
			return ir.Request{}, fmt.Errorf("'%s' is not supported on this surface", key)
		}
	}
	if bestOf, ok := coerceInt(body["best_of"]); ok && bestOf > 1 {
		return ir.Request{}, fmt.Errorf("'best_of' must be 1 (unsupported on this surface)")
	}
	if suffix := strOrEmpty(body["suffix"]); suffix != "" {
		return ir.Request{}, fmt.Errorf("'suffix' is not supported on this surface")
	}

	text, err := completionsPrompt(body["prompt"])
	if err != nil {
		return ir.Request{}, err
	}

	var g ir.GenParams
	g.Temperature = floatPtr(body["temperature"])
	g.TopP = floatPtr(body["top_p"])
	g.MaxTokens = chatMaxTokens(body)
	g.Stop = stopList(body["stop"])
	g.Seed = intPtr(body["seed"])

	streamOpts, _ := body["stream_options"].(map[string]any)
	includeUsage := false
	if streamOpts != nil {
		includeUsage, _ = streamOpts["include_usage"].(bool)
	}

	return ir.Request{
		Model: model,
		Messages: []ir.Message{{
			Role:  ir.RoleUser,
			Parts: []ir.Part{ir.NewTextPart(text)},
		}},
		GenParams:                 g,
		Stream:                    boolValue(body["stream"]),
		StreamOptionsIncludeUsage: includeUsage,
		Extras:                    carryExtras(body, completionsKnownKeys),
	}, nil
}

// completionsPrompt resolves the wire prompt to the text of one user turn.
func completionsPrompt(raw any) (string, error) {
	switch p := raw.(type) {
	case nil:
		return "", fmt.Errorf("'prompt' is required")
	case string:
		return p, nil
	case []any:
		if len(p) == 0 {
			return "", nil
		}
		for _, item := range p {
			if _, ok := item.(string); !ok {
				return "", fmt.Errorf("token-id prompts are not supported; send 'prompt' as a string")
			}
		}
		if len(p) > 1 {
			return "", fmt.Errorf("'prompt' may hold at most one string (multi-prompt is unsupported)")
		}
		return p[0].(string), nil
	}
	return "", fmt.Errorf("'prompt' must be a string")
}

// --- non-streaming encode ---------------------------------------------------

func encodeCompletionsResponse(turn ir.AssistantTurn, model string) map[string]any {
	finish := openAIFinish(turn.StopReason)
	if finish == "tool_calls" {
		// This dialect has no tool protocol, so a tool turn is a plain stop.
		finish = "stop"
	}
	u := turn.Usage
	prompt := u.PromptTokens
	if u.CachedInPrompt == nil || !*u.CachedInPrompt {
		prompt += u.CachedTokens
	}
	return map[string]any{
		"id":      respID("cmpl-"),
		"object":  "text_completion",
		"created": unixNow(),
		"model":   model,
		"choices": []any{map[string]any{
			"index": 0, "text": turn.Text, "logprobs": nil, "finish_reason": finish,
		}},
		"usage": map[string]any{
			"prompt_tokens":     prompt,
			"completion_tokens": u.CompletionTokens,
			"total_tokens":      prompt + u.CompletionTokens,
		},
	}
}

// --- streaming encode -------------------------------------------------------

type completionsStreamEncoder struct {
	id      string
	created int64
	model   string
	usage   *streaming.UsageFinal
	stop    ir.StopReason
	dropped bool
	errored bool
}

func newCompletionsStreamEncoder(model, reqID string) *completionsStreamEncoder {
	return &completionsStreamEncoder{
		id:      "cmpl-" + reqID,
		created: unixNow(),
		model:   model,
		stop:    ir.StopStop,
	}
}

func (e *completionsStreamEncoder) chunk(text string, finish any) string {
	return mustJSON(map[string]any{
		"id":      e.id,
		"object":  "text_completion",
		"created": e.created,
		"model":   e.model,
		"choices": []any{map[string]any{
			"index": 0, "text": text, "finish_reason": finish,
		}},
	})
}

func (e *completionsStreamEncoder) Start() []string { return []string{e.chunk("", nil)} }

func (e *completionsStreamEncoder) Encode(d streaming.Delta) []string {
	switch v := d.(type) {
	case streaming.TextDelta:
		return []string{e.chunk(v.Text, nil)}
	case streaming.ToolCallOpen, streaming.ToolCallArgsDelta, streaming.ToolCallClose:
		// No tool protocol on this surface.
		if _, ok := v.(streaming.ToolCallClose); !ok {
			e.dropped = true
		}
		return nil
	case streaming.UsageFinal:
		u := v
		e.usage = &u
		return nil
	case streaming.Finish:
		e.stop = v.StopReason
		return nil
	case streaming.StreamError:
		e.errored = true
		eType := streaming.StreamErrorType(v)
		return []string{mustJSON(map[string]any{
			"error": map[string]any{
				"message": v.ErrorText(), "type": eType, "code": eType,
			},
		})}
	}
	return nil
}

func (e *completionsStreamEncoder) End() []string {
	if e.errored {
		return []string{"[DONE]"}
	}
	stop := e.stop
	if e.dropped || stop == ir.StopToolCall {
		stop = ir.StopStop
	}
	out := []string{e.chunk("", openAIFinish(stop))}
	if e.usage != nil {
		prompt := e.usage.Prompt
		if e.usage.CachedInPrompt == nil || !*e.usage.CachedInPrompt {
			prompt += e.usage.Cached
		}
		out = append(out, mustJSON(map[string]any{
			"id":      e.id,
			"object":  "text_completion",
			"created": e.created,
			"model":   e.model,
			"choices": []any{},
			"usage": map[string]any{
				"prompt_tokens":     prompt,
				"completion_tokens": e.usage.Output,
				"total_tokens":      prompt + e.usage.Output,
			},
		}))
	}
	return append(out, "[DONE]")
}
