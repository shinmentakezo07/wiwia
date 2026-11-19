package providers

import (
	"regexp"
	"strings"
)

// WorkBuddy-specific request shape, ported from workbuddy_adapter.py:
// the upstream rejects a request whose first message is not `system`, and it
// refuses the OpenAI object form of tool_choice (and tool_choice=none next to
// a tools array). It also moderates the client's own system prompt, so the
// Claude Code/Codex identity fingerprints are rewritten before the request
// leaves the gateway.

const workBuddyDefaultSystem = "You are a helpful assistant."

var workBuddySanitizeFeatures = []string{
	"x-anthropic-billing-header",
	"cc_entrypoint=",
	"Claude Code",
	"You are an interactive CLI tool",
	"is an agentic coding CLI",
	"OpenAI's command-line coding agent",
	"Main branch (",
}

var (
	workBuddyHeaderRE = regexp.MustCompile(`(?i)x-anthropic-billing-header:[^;\n]*;?\s*`)
	workBuddyKVRE     = regexp.MustCompile(`(?i)\bcc_[a-z0-9_]+=[^;\n]*;?\s*`)
)

var workBuddyIdentityRewrites = [][2]string{
	{"Anthropic's official CLI for Claude", "Anthropic's official CLI tool for Claude"},
	{"You are an interactive CLI tool", "You are an interactive CLI utility"},
	{"is an agentic coding CLI", "is an agentic coding utility"},
	{"OpenAI's command-line coding agent", "OpenAI's command-line coding utility"},
}

var workBuddyRewrites = [][2]string{
	{"Main branch (you will usually use this for PRs)", "Default branch (you will usually use this for PRs)"},
}

func workBuddyHasFingerprint(text string) bool {
	for _, f := range workBuddySanitizeFeatures {
		if strings.Contains(text, f) {
			return true
		}
	}
	return workBuddyHeaderRE.MatchString(text)
}

func sanitizeWorkBuddyText(text string) string {
	if !workBuddyHasFingerprint(text) {
		return text
	}
	for _, r := range workBuddyRewrites {
		text = strings.ReplaceAll(text, r[0], r[1])
	}
	for _, r := range workBuddyIdentityRewrites {
		text = strings.ReplaceAll(text, r[0], r[1])
	}
	text = workBuddyHeaderRE.ReplaceAllString(text, "")
	if strings.Contains(text, "cc_") {
		prev := ""
		for prev != text {
			prev = text
			text = workBuddyKVRE.ReplaceAllString(text, "")
		}
	}
	return strings.TrimSpace(text)
}

func sanitizeWorkBuddyContent(value any) any {
	switch v := value.(type) {
	case string:
		return sanitizeWorkBuddyText(v)
	case []any:
		for _, raw := range v {
			part := AsDict(raw)
			if txt, ok := part["text"].(string); ok {
				part["text"] = sanitizeWorkBuddyText(txt)
			}
		}
	}
	return value
}

func applyWorkBuddyMessages(body map[string]any) {
	messages, _ := body["messages"].([]any)
	if len(messages) == 0 || AsString(AsDict(messages[0])["role"]) != "system" {
		messages = append([]any{map[string]any{
			"role": "system", "content": workBuddyDefaultSystem,
		}}, messages...)
		body["messages"] = messages
	}
	for _, raw := range messages {
		msg := AsDict(raw)
		if content, ok := msg["content"]; ok {
			msg["content"] = sanitizeWorkBuddyContent(content)
		}
	}
}

func normalizeWorkBuddyToolChoice(body map[string]any) {
	tc, ok := body["tool_choice"]
	if !ok {
		return
	}
	suppress := func() {
		delete(body, "tools")
		delete(body, "functions")
	}
	switch v := tc.(type) {
	case string:
		if strings.EqualFold(strings.TrimSpace(v), "none") {
			delete(body, "tool_choice")
			suppress()
		}
	case map[string]any:
		typ := strings.ToLower(strings.TrimSpace(AsString(v["type"])))
		switch typ {
		case "none":
			delete(body, "tool_choice")
			suppress()
		case "auto", "required":
			body["tool_choice"] = typ
		case "function":
			name := AsString(AsDict(v["function"])["name"])
			if strings.TrimSpace(name) == "" {
				name = AsString(v["name"])
			}
			name = strings.TrimSpace(name)
			if name == "" {
				body["tool_choice"] = "auto"
			} else {
				body["tool_choice"] = name
			}
		default:
			delete(body, "tool_choice")
		}
	default:
		delete(body, "tool_choice")
	}
}
