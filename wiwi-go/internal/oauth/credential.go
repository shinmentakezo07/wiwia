package oauth

import (
	"encoding/base64"
	"encoding/json"
	"strings"
	"time"
)

// Credential is the OAuth state stored in a provider key for the OAuth-backed
// providers (Cline, WorkBuddy, Hermes).
type Credential struct {
	AccessToken  string `json:"access_token"`
	RefreshToken string `json:"refresh_token,omitempty"`
	ExpiresAt    int64  `json:"expires_at,omitempty"` // unix seconds
	Email        string `json:"email,omitempty"`
	UID          string `json:"uid,omitempty"`
	EnterpriseID string `json:"enterprise_id,omitempty"`
	Nickname     string `json:"nickname,omitempty"`
	Domain       string `json:"domain,omitempty"`
	Realm        string `json:"realm,omitempty"`
}

// Encode serializes a credential with a provider prefix.
func Encode(prefix string, c Credential) string {
	raw, _ := json.Marshal(c)
	return prefix + base64.RawURLEncoding.EncodeToString(raw)
}

// Decode parses a prefixed credential (or a bare JSON object).
func Decode(prefix, secret string) (Credential, bool) {
	secret = strings.TrimSpace(secret)
	var c Credential
	if strings.HasPrefix(secret, prefix) {
		b, err := base64.RawURLEncoding.DecodeString(strings.TrimPrefix(secret, prefix))
		if err != nil {
			return c, false
		}
		if json.Unmarshal(b, &c) != nil || c.AccessToken == "" {
			return c, false
		}
		return c, true
	}
	if strings.HasPrefix(secret, "{") {
		if json.Unmarshal([]byte(secret), &c) == nil && c.AccessToken != "" {
			return c, true
		}
	}
	return c, false
}

// ExpiresWithinLead reports whether the token is expired or within leadS.
func ExpiresWithinLead(expiresAt int64, leadS int64) bool {
	if expiresAt == 0 {
		return true
	}
	return time.Now().Unix()+leadS >= expiresAt
}

// ResolveExpiresAt turns a wire `expires_in` value into an absolute unix
// expiry. A missing, non-numeric, boolean, zero, or <=lead value is unusable:
// recording it would leave the token inside its own refresh window (or in the
// past), so the single-use refresh grant would be burned on every sweep until
// the upstream answers invalid_grant (AUDIT #425). Such values fall back to
// 2*lead, strictly beyond the lead so the next due-check returns false. A
// usable value is honoured verbatim.
func ResolveExpiresAt(expiresIn any, leadS int64, now int64) int64 {
	if b, ok := expiresIn.(bool); ok && b {
		return now + 2*leadS
	}
	secs, ok := toInt64(expiresIn)
	if !ok || secs <= leadS {
		return now + 2*leadS
	}
	return now + secs
}

// Bearer returns the access token for an Authorization header.
func (c Credential) Bearer() string { return "Bearer " + c.AccessToken }
