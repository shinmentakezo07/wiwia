package oauth

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"time"
)

const (
	ClineAppBase      = "https://app.cline.bot"
	ClineAPIBase      = "https://api.cline.bot/api/v1"
	ClinePrefix       = "cline:"
	ClineRefreshLeadS = 300
)

var (
	clineFieldRe = regexp.MustCompile(`"(accessToken|refreshToken|email|expiresAt|firstName|lastName|name)"\s*:\s*"((?:[^"\\]|\\.)*)"`)
	clineTruncRe = regexp.MustCompile(`"accessToken"\s*:\s*"(.+)$`)
)

// ClineBuildAuthURL returns the browser login entry point.
func ClineBuildAuthURL(callbackURL string) string {
	q := url.Values{"callback_url": {callbackURL}, "redirect_uri": {callbackURL}, "client_type": {"extension"}}
	return ClineAPIBase + "/auth/authorize?" + q.Encode()
}

// extractClineCode pulls the base64 blob out of a bare blob, a URL-encoded
// blob, or a full callback URL containing ?code=.
func extractClineCode(raw string) string {
	s := strings.TrimSpace(raw)
	if strings.ContainsAny(s, "?#") {
		if u, err := url.Parse(s); err == nil {
			if v := u.Query().Get("code"); v != "" {
				s = v
			}
		}
	}
	if strings.Contains(s, "%") {
		if unescaped, err := url.QueryUnescape(s); err == nil {
			s = unescaped
		}
	}
	return s
}

func clineB64Decode(raw string) ([]byte, error) {
	c := strings.TrimSpace(raw)
	for len(c)%4 == 1 {
		c = c[:len(c)-1]
	}
	if pad := len(c) % 4; pad != 0 {
		c += strings.Repeat("=", 4-pad)
	}
	for _, enc := range []*base64.Encoding{base64.StdEncoding, base64.URLEncoding, base64.RawStdEncoding, base64.RawURLEncoding} {
		if b, err := enc.DecodeString(c); err == nil {
			return b, nil
		}
	}
	return nil, fmt.Errorf("not a base64 token blob")
}

func clineJWTClaims(token string) map[string]any {
	parts := strings.Split(token, ".")
	if len(parts) < 2 {
		return nil
	}
	p := parts[1]
	if pad := len(p) % 4; pad != 0 {
		p += strings.Repeat("=", 4-pad)
	}
	b, err := base64.URLEncoding.DecodeString(p)
	if err != nil {
		return nil
	}
	var data map[string]any
	if json.Unmarshal(b, &data) != nil {
		return nil
	}
	return data
}

// ClineExchangeCode exchanges a pasted Cline auth code for tokens (offline).
func ClineExchangeCode(code string) (map[string]any, error) {
	raw := extractClineCode(code)
	decoded, err := clineB64Decode(raw)
	if err != nil {
		return nil, fmt.Errorf("code is not a base64 token blob")
	}
	fields := map[string]string{}
	for _, m := range clineFieldRe.FindAllSubmatch(decoded, -1) {
		fields[string(m[1])] = string(m[2])
	}
	access := fields["accessToken"]
	if access == "" {
		if m := clineTruncRe.FindSubmatch(decoded); m != nil {
			access = string(m[1])
		}
	}
	if access == "" {
		return nil, fmt.Errorf("code payload missing accessToken")
	}
	out := map[string]any{
		"access_token":  access,
		"refresh_token": fields["refreshToken"],
		"expires_at":    fields["expiresAt"],
		"email":         fields["email"],
	}
	if claims := clineJWTClaims(access); claims != nil {
		if out["email"] == "" {
			out["email"], _ = claims["email"].(string)
		}
		if out["expires_at"] == "" {
			if exp, ok := claims["exp"].(float64); ok {
				out["expires_at"] = time.Unix(int64(exp), 0).UTC().Format(time.RFC3339)
			}
		}
	}
	return out, nil
}

// ClineRefresh refreshes an access token. Returns (nil, nil) on a transient
// failure; an error marked unrecoverable when the refresh token is dead.
func ClineRefresh(refreshToken string) (map[string]any, error) {
	body, _ := json.Marshal(map[string]any{
		"refreshToken": refreshToken, "grantType": "refresh_token", "clientType": "extension",
	})
	req, _ := http.NewRequest("POST", ClineAPIBase+"/auth/refresh", strings.NewReader(string(body)))
	req.Header.Set("Content-Type", "application/json")
	resp, err := httpClient().Do(req)
	if err != nil {
		return nil, nil
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		var eb map[string]any
		_ = json.Unmarshal(raw, &eb)
		if e, ok := eb["error"].(map[string]any); ok {
			if code, _ := e["code"].(string); code == "invalid_grant" || code == "invalid_request" {
				return nil, fmt.Errorf("unrecoverable_refresh_error: %s", code)
			}
		}
		return nil, nil
	}
	var payload map[string]any
	if json.Unmarshal(raw, &payload) != nil {
		return nil, nil
	}
	data := payload
	if d, ok := payload["data"].(map[string]any); ok {
		data = d
	}
	access, _ := data["accessToken"].(string)
	if access == "" {
		return nil, nil
	}
	out := map[string]any{"access_token": access}
	if rt, _ := data["refreshToken"].(string); rt != "" {
		out["refresh_token"] = rt
	} else {
		out["refresh_token"] = refreshToken
	}
	if exp, _ := data["expiresAt"].(string); exp != "" {
		out["expires_at"] = exp
	}
	return out, nil
}

// ClineFingerprint is a stable short hash of an access token (for logs).
func ClineFingerprint(access string) string {
	sum := sha256.Sum256([]byte(access))
	return hex.EncodeToString(sum[:8])
}

// ParseISOTime parses an ISO-8601 timestamp to unix seconds.
func ParseISOTime(s string) (int64, bool) {
	if s == "" {
		return 0, false
	}
	for _, layout := range []string{time.RFC3339, "2006-01-02T15:04:05", "2006-01-02T15:04:05Z07:00"} {
		if t, err := time.Parse(layout, s); err == nil {
			return t.Unix(), true
		}
	}
	return 0, false
}
