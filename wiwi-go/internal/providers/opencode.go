package providers

import (
	"crypto/sha256"
	"encoding/hex"
	"strings"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
	"wiwi-go/internal/versions"
)

// OpencodeAdapter routes each model to the wire its Zen catalog serves:
//
//	responses  gpt-*, grok-*, muse-spark-*, muse-ember-*, muse-wonder-*
//	messages   claude-*, union-alpha, qwen3.8-flash,
//	           qwen3.7-*, qwen3.6-*, qwen3.5-*
//	gemini     gemini-*
//	chat       everything else
//
// Prefix guessing alone is wrong (qwen3.8-max is chat while qwen3.8-flash is
// messages), so this is an explicit table. Each wire also has its own canonical
// base and credential header (Python _ROUTE_BASES / _CREDENTIAL_HEADER).
type OpencodeAdapter struct {
	modelID string
	sub     Adapter

	decoyNames   map[string]bool
	decoyIndices map[int]bool
	keptRealCall bool
}

const opencodeDefaultBase = "https://opencode.ai/inference/openai/v1"

func opencodeRouteBases(route string) string {
	switch route {
	case "messages":
		return "https://opencode.ai/inference/anthropic/v1"
	case "gemini":
		return "https://opencode.ai/inference/google/v1beta"
	}
	return opencodeDefaultBase
}

func opencodeRoute(model string) string {
	m := strings.ToLower(strings.TrimSpace(model))
	switch {
	case strings.HasPrefix(m, "gpt-"), strings.HasPrefix(m, "grok-"),
		strings.HasPrefix(m, "muse-spark-"), strings.HasPrefix(m, "muse-ember-"),
		strings.HasPrefix(m, "muse-wonder-"):
		return "responses"
	case strings.HasPrefix(m, "claude-"), strings.HasPrefix(m, "union-alpha"),
		strings.HasPrefix(m, "qwen3.8-flash"), strings.HasPrefix(m, "qwen3.7-"),
		strings.HasPrefix(m, "qwen3.6-"), strings.HasPrefix(m, "qwen3.5-"):
		return "messages"
	case strings.HasPrefix(m, "gemini-"):
		return "gemini"
	}
	return "chat"
}

// opencodeBase keeps an explicit deployment base_url (the legacy override) but
// treats the old/default OpenAI roots as unset, so messages and gemini reach
// their own canonical roots.
func opencodeBase(baseURL, route string) string {
	b := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	switch b {
	case "", "https://opencode.ai/inference/openai/v1", "https://opencode.ai/inference/v1":
		return opencodeRouteBases(route)
	}
	return b
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

// opencodeSessionID mirrors the v2 client's `ses_` + 12 hex + 14 base62 shape.
// It is deterministic per credential, as the real client reuses one session id.
func opencodeSessionID(label, secret string) string {
	sum := sha256.Sum256([]byte("opencode:" + label + ":" + secret))
	const alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
	id := "ses_" + hex.EncodeToString(sum[:6])
	for _, b := range sum[6:20] {
		id += string(alphabet[int(b)%len(alphabet)])
	}
	return id
}

func (a *OpencodeAdapter) Headers(key ProviderKeyRef) map[string]string {
	route := "chat"
	if a.modelID != "" {
		route = opencodeRoute(a.modelID)
	}
	h := map[string]string{
		"Content-Type": "application/json",
		"User-Agent":   versions.OpenCodeUA(),
		"X-Title":      "opencode",
	}
	if route == "messages" {
		h["anthropic-version"] = "2023-06-01"
	}
	secret := strings.TrimSpace(key.Secret)
	if strings.EqualFold(secret, "anonymous") {
		if route == "chat" || route == "responses" {
			h["Authorization"] = "Bearer public"
		}
	} else {
		switch route {
		case "messages":
			h["x-api-key"] = secret
		case "gemini":
			h["x-goog-api-key"] = secret
		default:
			h["Authorization"] = "Bearer " + secret
		}
	}
	session := opencodeSessionID(key.Label, key.Secret)
	h["x-opencode-session"] = session
	h["x-opencode-session-id"] = session
	h["x-opencode-client"] = "cli"
	h["x-opencode-project"] = "global"
	return h
}

func (a *OpencodeAdapter) BuildURL(baseURL, modelID string, stream bool) string {
	route := opencodeRoute(modelID)
	base := opencodeBase(baseURL, route)
	switch route {
	case "responses":
		return base + "/responses"
	case "messages":
		return base + "/messages"
	case "gemini":
		return (&GeminiAdapter{}).BuildURL(base, modelID, stream)
	}
	return base + "/chat/completions"
}

func (a *OpencodeAdapter) EncodeRequest(req ir.Request, modelID string, params map[string]any) (map[string]any, error) {
	a.modelID = modelID
	a.sub = a.subFor(modelID)
	body, err := a.sub.EncodeRequest(req, modelID, params)
	if err != nil {
		return nil, err
	}
	// Zen answers a caller-supplied metadata block with
	// 400 [unsupported_metadata]; an operator can still force it via
	// extra_body.metadata on the deployment.
	if _, ok := body["metadata"]; ok {
		if _, declared := params["metadata"]; !declared {
			delete(body, "metadata")
		}
	}
	if chat, ok := a.sub.(*OpenAIAdapter); ok && len(chat.opencodeDecoys) > 0 {
		a.decoyNames = chat.opencodeDecoys
	}
	if _, ok := a.sub.(*ResponsesAdapter); ok && isOpencodeFreeModel(modelID) && opencodeCloakRequested(params) {
		a.decoyNames = applyOpencodeResponsesCloak(body)
	}
	if _, ok := a.sub.(*ResponsesAdapter); ok && isOpencodeFreeModel(modelID) {
		forceOpencodeAutoToolChoice(body, modelID)
	}
	return body, nil
}

func (a *OpencodeAdapter) DecodeResponse(status int, body []byte) (ir.AssistantTurn, error) {
	if a.sub == nil {
		a.sub = a.subFor(a.modelID)
	}
	turn, err := a.sub.DecodeResponse(status, body)
	if err != nil {
		return turn, err
	}
	return a.filterDecoyTurn(turn), nil
}

func (a *OpencodeAdapter) filterDecoyTurn(turn ir.AssistantTurn) ir.AssistantTurn {
	if len(a.decoyNames) == 0 || len(turn.ToolCalls) == 0 {
		return turn
	}
	kept := turn.ToolCalls[:0]
	for _, tc := range turn.ToolCalls {
		if !a.decoyNames[tc.Name] {
			kept = append(kept, tc)
		}
	}
	if len(kept) == len(turn.ToolCalls) {
		return turn
	}
	turn.ToolCalls = kept
	if turn.StopReason == ir.StopToolCall && len(kept) == 0 {
		turn.StopReason = ir.StopStop
	}
	return turn
}

func (a *OpencodeAdapter) DecodeStreamEvent(event, data string) ([]streaming.Delta, error) {
	if a.sub == nil {
		a.sub = a.subFor(a.modelID)
	}
	deltas, err := a.sub.DecodeStreamEvent(event, data)
	if err != nil {
		return nil, err
	}
	return a.filterDecoys(deltas), nil
}

func (a *OpencodeAdapter) filterDecoys(deltas []streaming.Delta) []streaming.Delta {
	if len(a.decoyNames) == 0 {
		return deltas
	}
	out := make([]streaming.Delta, 0, len(deltas))
	for _, d := range deltas {
		switch v := d.(type) {
		case streaming.ToolCallOpen:
			if a.decoyNames[v.Name] {
				if a.decoyIndices == nil {
					a.decoyIndices = map[int]bool{}
				}
				a.decoyIndices[v.Index] = true
				continue
			}
			a.keptRealCall = true
		case streaming.ToolCallArgsDelta:
			if a.decoyIndices[v.Index] {
				continue
			}
		case streaming.ToolCallClose:
			if a.decoyIndices[v.Index] {
				continue
			}
		case streaming.Finish:
			if v.StopReason == ir.StopToolCall && !a.keptRealCall {
				v.StopReason = ir.StopStop
			}
			d = v
		}
		out = append(out, d)
	}
	return out
}

func (a *OpencodeAdapter) Reset() {
	a.modelID = ""
	a.sub = nil
	a.decoyNames = nil
	a.decoyIndices = nil
	a.keptRealCall = false
}

func init() {
	Register("opencode", func() Adapter { return &OpencodeAdapter{} })
}
