package providers

import (
	"strings"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
	"wiwi-go/internal/versions"
)

// OpencodeAdapter routes each model to the wire its Zen catalog serves:
//
//	responses  gpt-*, grok-*, muse-spark-*          POST {base}/responses
//	messages   claude-*, union-alpha, qwen3.8-flash,
//	           qwen3.7-*, qwen3.6-*, qwen3.5-*        POST {base}/messages
//	gemini     gemini-*                             POST {base}/models/{id}:...
//	chat       everything else                      POST {base}/chat/completions
//
// Prefix guessing alone is wrong (qwen3.8-max is chat while qwen3.8-flash is
// messages), so this is an explicit table.
type OpencodeAdapter struct {
	modelID string
	sub     Adapter
}

func opencodeRoute(model string) string {
	switch {
	case strings.HasPrefix(model, "gpt-"), strings.HasPrefix(model, "grok-"),
		strings.HasPrefix(model, "muse-spark-"):
		return "responses"
	case strings.HasPrefix(model, "claude-"), strings.HasPrefix(model, "union-alpha"),
		strings.HasPrefix(model, "qwen3.8-flash"), strings.HasPrefix(model, "qwen3.7-"),
		strings.HasPrefix(model, "qwen3.6-"), strings.HasPrefix(model, "qwen3.5-"):
		return "messages"
	case strings.HasPrefix(model, "gemini-"):
		return "gemini"
	}
	return "chat"
}

func (a *OpencodeAdapter) subFor(model string) Adapter {
	switch opencodeRoute(model) {
	case "responses":
		return &ResponsesAdapter{providerType: "opencode"}
	case "messages":
		return &AnthropicAdapter{}
	case "gemini":
		return &GeminiAdapter{}
	}
	return &OpenAIAdapter{providerType: "opencode"}
}

func (a *OpencodeAdapter) ProviderType() string { return "opencode" }

func (a *OpencodeAdapter) Headers(key ProviderKeyRef) map[string]string {
	return map[string]string{
		"Authorization": "Bearer " + key.Secret,
		"Content-Type":  "application/json",
		"User-Agent":    versions.OpenCodeUA(),
	}
}

func (a *OpencodeAdapter) BuildURL(baseURL, modelID string, stream bool) string {
	base := strings.TrimRight(baseURL, "/")
	switch opencodeRoute(modelID) {
	case "responses":
		return base + "/responses"
	case "messages":
		return base + "/messages"
	case "gemini":
		return (&GeminiAdapter{}).BuildURL(baseURL, modelID, stream)
	}
	return base + "/chat/completions"
}

func (a *OpencodeAdapter) EncodeRequest(req ir.Request, modelID string, params map[string]any) (map[string]any, error) {
	a.modelID = modelID
	a.sub = a.subFor(modelID)
	return a.sub.EncodeRequest(req, modelID, params)
}

func (a *OpencodeAdapter) DecodeResponse(status int, body []byte) (ir.AssistantTurn, error) {
	if a.sub == nil {
		a.sub = a.subFor(a.modelID)
	}
	return a.sub.DecodeResponse(status, body)
}

func (a *OpencodeAdapter) DecodeStreamEvent(event, data string) ([]streaming.Delta, error) {
	if a.sub == nil {
		a.sub = a.subFor(a.modelID)
	}
	return a.sub.DecodeStreamEvent(event, data)
}

func (a *OpencodeAdapter) Reset() {
	a.modelID = ""
	a.sub = nil
}

func init() {
	Register("opencode", func() Adapter { return &OpencodeAdapter{} })
}
