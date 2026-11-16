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

const (
	WorkBuddyChatBaseCN     = "https://copilot.tencent.com"
	WorkBuddyChatBaseGlobal = "https://www.workbuddy.ai"
	WorkBuddyOriginCN       = "https://www.codebuddy.cn"
	WorkBuddyOriginGlobal   = "https://www.workbuddy.ai"
	WorkBuddyPrefix         = "workbuddy:"
	WorkBuddyRefreshLeadS   = 300

	workbuddyStatePath   = "/v2/plugin/auth/state"
	workbuddyTokenPath   = "/v2/plugin/auth/token"
	workbuddyAccountPath = "/v2/plugin/login/account"
	workbuddyRefreshPath = "/v2/plugin/auth/token/refresh"
)

func wbRealm(realm string) string {
	if strings.EqualFold(realm, "global") {
		return "global"
	}
	return "cn"
}

func wbBase(realm string) string {
	if wbRealm(realm) == "global" {
		return WorkBuddyChatBaseGlobal
	}
	return WorkBuddyChatBaseCN
}

func wbOrigin(realm string) string {
	if wbRealm(realm) == "global" {
		return WorkBuddyOriginGlobal
	}
	return WorkBuddyOriginCN
}

func wbHeaders(origin string) map[string]string {
	return map[string]string{
		"Content-Type":     "application/json",
		"Accept":           "application/json, text/plain, */*",
		"X-Requested-With": "XMLHttpRequest",
		"Origin":           origin,
		"Referer":          origin + "/",
	}
}

// acceptedAuthURL restricts the returned browser URL to known hosts.
func acceptedAuthURL(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || (u.Scheme != "https" && u.Scheme != "http") {
		return ""
	}
	switch u.Hostname() {
	case "www.workbuddy.ai", "workbuddy.ai", "www.codebuddy.cn", "codebuddy.cn", "copilot.tencent.com":
		return raw
	}
	return ""
}

// WorkBuddyStartLogin opens a browser login and returns the polling state and
// the URL the operator opens.
func WorkBuddyStartLogin(realm string) (state, authURL string, err error) {
	realm = wbRealm(realm)
	base := wbBase(realm)
	origin := wbOrigin(realm)
	req, _ := http.NewRequest("POST", base+workbuddyStatePath+"?platform=CLI", nil)
	for k, v := range wbHeaders(origin) {
		req.Header.Set(k, v)
	}
	resp, err := httpClient().Do(req)
	if err != nil {
		return "", "", err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return "", "", fmt.Errorf("login start http %d: %s", resp.StatusCode, truncate(string(body), 200))
	}
	var env map[string]any
	if json.Unmarshal(body, &env) != nil {
		return "", "", fmt.Errorf("login start parse failed")
	}
	if code, _ := toInt64(env["code"]); code != 0 {
		return "", "", fmt.Errorf("login start code=%v", env["code"])
	}
	data, _ := env["data"].(map[string]any)
	if data == nil {
		return "", "", fmt.Errorf("login start: no data")
	}
	state, _ = data["state"].(string)
	if strings.TrimSpace(state) == "" {
		return "", "", fmt.Errorf("login start: response carried no state")
	}
	authURL = acceptedAuthURL(str(data["authUrl"]))
	return state, authURL, nil
}

// WorkBuddyPollLogin polls a pending browser login. It returns (nil, nil) while
// the user has not approved.
func WorkBuddyPollLogin(state, realm string) (*Credential, error) {
	realm = wbRealm(realm)
	base := wbBase(realm)
	origin := wbOrigin(realm)
	req, _ := http.NewRequest("GET", base+workbuddyTokenPath+"?state="+url.QueryEscape(state), nil)
	for k, v := range wbHeaders(origin) {
		req.Header.Set(k, v)
	}
	resp, err := httpClient().Do(req)
	if err != nil {
		return nil, nil // transient
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("login poll http %d", resp.StatusCode)
	}
	var env map[string]any
	if json.Unmarshal(body, &env) != nil {
		return nil, nil
	}
	if code, _ := toInt64(env["code"]); code != 0 {
		return nil, nil // pending (e.g. 11217)
	}
	data, _ := env["data"].(map[string]any)
	if data == nil {
		return nil, nil
	}
	access := str(data["accessToken"])
	if access == "" {
		return nil, nil
	}
	domain := str(data["domain"])
	if domain == "" && realm == "global" {
		domain = WorkBuddyChatBaseGlobal
	}
	c := &Credential{
		AccessToken:  access,
		RefreshToken: str(data["refreshToken"]),
		Domain:       domain,
		Realm:        realm,
	}
	if exp, ok := toInt64(data["expiresIn"]); ok && exp > 0 {
		c.ExpiresAt = time.Now().Unix() + exp
	} else {
		c.ExpiresAt = time.Now().Unix() + 2*WorkBuddyRefreshLeadS
	}
	// best-effort identity fill
	fillWorkBuddyAccount(base, origin, state, c)
	return c, nil
}

func fillWorkBuddyAccount(base, origin, state string, c *Credential) {
	req, _ := http.NewRequest("GET", base+workbuddyAccountPath+"?state="+url.QueryEscape(state), nil)
	for k, v := range wbHeaders(origin) {
		req.Header.Set(k, v)
	}
	req.Header.Set("Authorization", "Bearer "+c.AccessToken)
	resp, err := httpClient().Do(req)
	if err != nil {
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return
	}
	body, _ := io.ReadAll(resp.Body)
	var env map[string]any
	if json.Unmarshal(body, &env) != nil {
		return
	}
	if code, _ := toInt64(env["code"]); code != 0 {
		return
	}
	data, _ := env["data"].(map[string]any)
	if data == nil {
		return
	}
	c.UID = str(data["uid"])
	c.EnterpriseID = str(data["enterpriseId"])
	c.Nickname = str(data["nickname"])
}

// WorkBuddyRefresh rotates the access token.
func WorkBuddyRefresh(c Credential) (*Credential, error) {
	if strings.TrimSpace(c.RefreshToken) == "" {
		return nil, fmt.Errorf("no refreshToken")
	}
	base := c.Domain
	if base == "" {
		base = wbBase(c.Realm)
	}
	req, _ := http.NewRequest("POST", strings.TrimRight(base, "/")+workbuddyRefreshPath, nil)
	for k, v := range wbHeaders(wbOrigin(c.Realm)) {
		req.Header.Set(k, v)
	}
	req.Header.Set("X-Refresh-Token", c.RefreshToken)
	req.Header.Set("X-Auth-Refresh-Source", "workbuddy")
	if c.EnterpriseID != "" {
		req.Header.Set("X-Enterprise-Id", c.EnterpriseID)
	}
	resp, err := httpClient().Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("refresh http %d: %s", resp.StatusCode, truncate(string(body), 200))
	}
	var env map[string]any
	if json.Unmarshal(body, &env) != nil {
		return nil, fmt.Errorf("refresh parse failed")
	}
	if code, _ := toInt64(env["code"]); code != 0 {
		return nil, fmt.Errorf("refresh code=%v", env["code"])
	}
	data, _ := env["data"].(map[string]any)
	if data == nil {
		return nil, fmt.Errorf("refresh: no data")
	}
	access := str(data["accessToken"])
	if access == "" {
		return nil, fmt.Errorf("refresh_failed: no accessToken")
	}
	nc := c
	nc.AccessToken = access
	if rt := str(data["refreshToken"]); rt != "" {
		nc.RefreshToken = rt
	}
	if d := str(data["domain"]); d != "" {
		nc.Domain = d
	}
	if exp, ok := toInt64(data["expiresIn"]); ok && exp > 0 {
		nc.ExpiresAt = time.Now().Unix() + exp
	} else {
		nc.ExpiresAt = time.Now().Unix() + 2*WorkBuddyRefreshLeadS
	}
	return &nc, nil
}

func str(v any) string {
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}
