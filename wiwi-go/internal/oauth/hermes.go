package oauth

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// Nous Portal (Hermes) OAuth device-code flow.
const (
	HermesPortalBase    = "https://portal.nousresearch.com"
	HermesClientID      = "hermes-cli"
	HermesScope         = "inference:invoke"
	HermesDeviceCodeURL = HermesPortalBase + "/api/oauth/device/code"
	HermesTokenURL      = HermesPortalBase + "/api/oauth/token"
	HermesGrantDevice   = "urn:ietf:params:oauth:grant-type:device_code"
	HermesPrefix        = "hermes:"
	HermesRefreshLeadS  = 120
)

func httpClient() *http.Client { return &http.Client{Timeout: 30 * time.Second} }

// HermesRequestDeviceCode starts a device-code authorization.
func HermesRequestDeviceCode() (map[string]any, error) {
	form := url.Values{"client_id": {HermesClientID}, "scope": {HermesScope}}
	req, _ := http.NewRequest("POST", HermesDeviceCodeURL, strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	resp, err := httpClient().Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("device code HTTP %d: %s", resp.StatusCode, truncate(string(body), 200))
	}
	var data map[string]any
	if err := json.Unmarshal(body, &data); err != nil {
		return nil, err
	}
	for _, f := range []string{"device_code", "user_code", "verification_uri", "expires_in", "interval"} {
		if _, ok := data[f]; !ok {
			return nil, fmt.Errorf("device code response missing %q", f)
		}
	}
	return data, nil
}

// HermesPollToken polls once. It returns (nil, nil) while the user has not
// approved (authorization_pending) and the token payload on success.
func HermesPollToken(deviceCode string) (map[string]any, error) {
	form := url.Values{
		"grant_type":  {HermesGrantDevice},
		"client_id":   {HermesClientID},
		"device_code": {deviceCode},
	}
	req, _ := http.NewRequest("POST", HermesTokenURL, strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	resp, err := httpClient().Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode == 200 {
		var payload map[string]any
		if err := json.Unmarshal(body, &payload); err != nil {
			return nil, err
		}
		if _, ok := payload["access_token"]; !ok {
			return nil, fmt.Errorf("token response missing access_token")
		}
		return payload, nil
	}
	var errBody map[string]any
	_ = json.Unmarshal(body, &errBody)
	code, _ := errBody["error"].(string)
	switch code {
	case "authorization_pending":
		return nil, nil
	case "slow_down":
		return nil, nil
	}
	if resp.StatusCode >= 500 || resp.StatusCode == 408 || resp.StatusCode == 429 {
		return nil, nil
	}
	desc, _ := errBody["error_description"].(string)
	if desc == "" {
		desc = code
	}
	if desc == "" {
		desc = "authentication failed"
	}
	return nil, fmt.Errorf("Nous Portal rejected the device code: %s", desc)
}

// HermesRefresh exchanges a refresh token (sent in the x-nous-refresh-token
// header, not the RFC 6749 body form).
func HermesRefresh(refreshToken string) (map[string]any, error) {
	form := url.Values{"grant_type": {"refresh_token"}, "client_id": {HermesClientID}}
	req, _ := http.NewRequest("POST", HermesTokenURL, strings.NewReader(form.Encode()))
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	req.Header.Set("Accept", "application/json")
	req.Header.Set("x-nous-refresh-token", refreshToken)
	resp, err := httpClient().Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode == 200 {
		var payload map[string]any
		if err := json.Unmarshal(body, &payload); err != nil {
			return nil, err
		}
		if _, ok := payload["access_token"]; !ok {
			return nil, fmt.Errorf("refresh response missing access_token")
		}
		return payload, nil
	}
	var errBody map[string]any
	_ = json.Unmarshal(body, &errBody)
	code, _ := errBody["error"].(string)
	desc, _ := errBody["error_description"].(string)
	if desc == "" {
		desc = "refresh token exchange failed"
	}
	return nil, fmt.Errorf("Nous Portal token refresh failed: %s (code=%s)", desc, code)
}

// HermesCredentialFromToken builds a Credential from a token payload.
func HermesCredentialFromToken(payload map[string]any) Credential {
	c := Credential{}
	c.AccessToken, _ = payload["access_token"].(string)
	c.RefreshToken, _ = payload["refresh_token"].(string)
	c.ExpiresAt = ResolveExpiresAt(payload["expires_in"], HermesRefreshLeadS, time.Now().Unix())
	return c
}

func toInt64(v any) (int64, bool) {
	switch x := v.(type) {
	case float64:
		return int64(x), true
	case int64:
		return x, true
	case int:
		return int64(x), true
	case json.Number:
		n, err := x.Int64()
		return n, err == nil
	}
	return 0, false
}

func truncate(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
