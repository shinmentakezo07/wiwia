package providers

import (
	"encoding/json"
	"fmt"
	"strings"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

// WiwiError is the normalized gateway error, rendered per-surface by wire codecs.
type WiwiError struct {
	Status       int
	EType        string
	Message      string
	Retryable    bool
	RetryAfter   float64
	UpstreamType string
	EgressScoped bool
}

func (e *WiwiError) Error() string { return e.Message }

var RetryableStatus = map[int]bool{408: true, 429: true, 500: true, 502: true, 503: true, 504: true, 529: true}

// StatusForKeyPool reports the status key-pool accounting cares about, or 0.
func StatusForKeyPool(e *WiwiError) int {
	if e == nil {
		return 0
	}
	if e.Status == 429 {
		return 429
	}
	if e.Status == 401 || e.Status == 403 {
		if e.EType == "permission_error" {
			return 0
		}
		return e.Status
	}
	if RetryableStatus[e.Status] {
		return e.Status
	}
	return 0
}

// ErrorFromProviderStatus classifies an upstream refusal and tags it with the
// vendor's own label where present.
func ErrorFromProviderStatus(status int, body []byte, provider string) *WiwiError {
	msg := ExtractErrorMessage(body)
	if msg == "" {
		msg = fmt.Sprintf("%s returned HTTP %d", provider, status)
	}
	etype := ExtractErrorType(body)
	e := &WiwiError{UpstreamType: etype}
	low := strings.ToLower(msg)
	set := func(s int, et string, m string, retry bool) *WiwiError {
		e.Status, e.EType, e.Message, e.Retryable = s, et, m, retry
		return e
	}
	switch status {
	case 401, 403:
		switch EntitlementKind(body, msg) {
		case "billing":
			return set(402, "permission_error", fmt.Sprintf("%s requires billing (%d): %s", provider, status, msg), true)
		case "policy":
			return set(403, "permission_error", fmt.Sprintf("%s denied access (%d): %s", provider, status, msg), true)
		case "network":
			e.EgressScoped = true
			return set(403, "permission_error", fmt.Sprintf("%s blocked this network (%d): %s", provider, status, msg), true)
		}
		return set(status, "authentication_error", fmt.Sprintf("%s rejected credentials (%d): %s", provider, status, msg), true)
	case 429:
		if IsQuotaRefusal(body, msg) {
			return set(402, "permission_error", fmt.Sprintf("%s quota exhausted (%d): %s", provider, status, msg), true)
		}
		return set(429, "rate_limit_error", fmt.Sprintf("%s rate limited: %s", provider, msg), true)
	case 504:
		return set(504, "timeout", fmt.Sprintf("%s timed out: %s", provider, msg), true)
	case 529:
		return set(529, "overloaded_error", fmt.Sprintf("%s overloaded (%d): %s", provider, status, msg), true)
	case 408, 500, 502, 503:
		return set(502, "api_connection_error", fmt.Sprintf("%s error %d: %s", provider, status, msg), true)
	case 400:
		if strings.Contains(low, "context") || strings.Contains(low, "maximum") || strings.Contains(low, "too long") {
			return set(400, "context_window_exceeded", msg, false)
		}
		for _, m := range []string{"unapproved channel", "illegal api invocation"} {
			if strings.Contains(low, m) {
				return set(400, "permission_error", fmt.Sprintf("%s refused this client/channel: %s", provider, msg), true)
			}
		}
		return set(400, "invalid_request_error", fmt.Sprintf("%s: %s", provider, msg), false)
	}
	return set(502, "api_error", fmt.Sprintf("%s error %d: %s", provider, status, msg), status >= 500)
}

var (
	BillingErrorTypes = map[string]bool{
		"FreeTierError": true, "CreditsError": true, "MonthlyLimitError": true,
		"UserLimitError": true, "FreeUsageLimitError": true, "GoUsageLimitError": true,
		"BlackUsageLimitError": true,
	}
	PolicyErrorTypes = map[string]bool{
		"RegionError": true, "DataPolicyError": true, "ModelError": true,
	}
	BillingMarkers = []string{"free tier can only be used", "no payment method", "insufficient balance", "spending limit", "usage limit reached", "subscription quota exceeded"}
	PolicyMarkers  = []string{"not available in your country", "requires explicit opt in", "model is disabled", "model is not supported"}
	NetworkMarkers = []string{"abusive traffic", "abuse traffic", "abuse of traffic", "from your current network"}
)

func EntitlementKind(body []byte, msg string) string {
	t := ExtractErrorType(body)
	if BillingErrorTypes[t] {
		return "billing"
	}
	if PolicyErrorTypes[t] {
		return "policy"
	}
	low := strings.ToLower(msg)
	for _, m := range NetworkMarkers {
		if strings.Contains(low, m) {
			return "network"
		}
	}
	for _, m := range BillingMarkers {
		if strings.Contains(low, m) {
			return "billing"
		}
	}
	for _, m := range PolicyMarkers {
		if strings.Contains(low, m) {
			return "policy"
		}
	}
	return ""
}

func IsQuotaRefusal(body []byte, msg string) bool {
	if msg == "" {
		msg = ExtractErrorMessage(body)
	}
	return EntitlementKind(body, msg) == "billing"
}

func ExtractErrorType(body []byte) string {
	var data map[string]any
	if json.Unmarshal(body, &data) != nil {
		return ""
	}
	if err, ok := data["error"].(map[string]any); ok {
		for _, k := range []string{"type", "code"} {
			if v, _ := err[k].(string); v != "" {
				return v
			}
		}
	}
	for _, k := range []string{"type", "code"} {
		if v, _ := data[k].(string); v != "" && v != "error" {
			return v
		}
	}
	return ""
}

func ExtractErrorMessage(body []byte) string {
	var data map[string]any
	if json.Unmarshal(body, &data) != nil {
		return truncate(string(body), 500)
	}
	if err, ok := data["error"].(map[string]any); ok {
		if msg, _ := err["message"].(string); msg != "" {
			if meta, _ := err["metadata"].(map[string]any); meta != nil {
				if raw, _ := meta["raw"].(string); raw != "" && raw != msg {
					pn, _ := meta["provider_name"].(string)
					if pn == "" {
						pn = "upstream"
					}
					return fmt.Sprintf("%s (%s: %s)", msg, pn, raw)
				}
			}
			return msg
		}
	} else if s, _ := data["error"].(string); s != "" {
		return s
	}
	if inner, ok := data["error"].(map[string]any); ok {
		if m, _ := inner["message"].(string); m != "" {
			return m
		}
	}
	return truncate(string(body), 500)
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}

// Helper coercion functions (total — never panic on junk upstream JSON).
func AsDict(v any) map[string]any {
	if m, ok := v.(map[string]any); ok {
		return m
	}
	return map[string]any{}
}

func AsList(v any) []any {
	if l, ok := v.([]any); ok {
		return l
	}
	return []any{}
}

func AsStr(v any, def string) string {
	if s, ok := v.(string); ok {
		return s
	}
	return def
}

func CoerceArgsFragment(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case map[string]any:
		b, _ := json.Marshal(x)
		return string(b)
	default:
		return ""
	}
}

// ProviderKeyRef is the credential an adapter receives for one request.
type ProviderKeyRef struct {
	Label  string
	Secret string
}

// Adapter is the outbound provider contract. All 13 provider types implement it.
type Adapter interface {
	ProviderType() string
	Headers(key ProviderKeyRef) map[string]string
	BuildURL(baseURL, modelID string, stream bool) string
	EncodeRequest(req ir.Request, modelID string, params map[string]any) (map[string]any, error)
	DecodeResponse(status int, body []byte) (ir.AssistantTurn, error)
	DecodeStreamEvent(event, data string) ([]streaming.Delta, error)
	Reset()
}

// StreamDelta aliases the canonical streaming delta type for adapter convenience.
type StreamDelta = streaming.Delta

func TakeWarnings(a any) []string {
	if w, ok := a.(interface{ Warnings() []string }); ok {
		return w.Warnings()
	}
	return nil
}
