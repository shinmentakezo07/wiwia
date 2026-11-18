package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"wiwi-go/internal/config"
	"wiwi-go/internal/oauth"
)

// -- credential storage -----------------------------------------------------

func (a *App) oauthProvider(name, providerType, baseURL string) *config.Provider {
	if p := a.findProvider(name); p != nil {
		return p
	}
	a.State.Cfg.Providers = append(a.State.Cfg.Providers, config.Provider{
		Name: name, ProviderType: providerType, BaseURL: baseURL, RoundRobin: true,
	})
	return &a.State.Cfg.Providers[len(a.State.Cfg.Providers)-1]
}

func (a *App) setOAuthKey(p *config.Provider, label, secret string) {
	for i := range p.Keys {
		if p.Keys[i].Label == label {
			p.Keys[i].Key = secret
			p.Keys[i].Enabled = true
			if p.Keys[i].Weight < 1 {
				p.Keys[i].Weight = 1
			}
			return
		}
	}
	p.Keys = append(p.Keys, config.KeyDef{Label: label, Key: secret, Weight: 1, Enabled: true})
}

func (a *App) removeOAuthKey(p *config.Provider, label string) bool {
	kept := p.Keys[:0]
	found := false
	for _, k := range p.Keys {
		if k.Label == label {
			found = true
			continue
		}
		kept = append(kept, k)
	}
	p.Keys = kept
	return found
}

func (a *App) oauthCredential(name, prefix string) (oauth.Credential, bool) {
	p := a.findProvider(name)
	if p == nil {
		return oauth.Credential{}, false
	}
	for _, k := range p.Keys {
		if c, ok := oauth.Decode(prefix, k.Key); ok {
			return c, true
		}
	}
	return oauth.Credential{}, false
}

func oauthStatus(c oauth.Credential, lead int64) gin.H {
	exp := ""
	if c.ExpiresAt > 0 {
		exp = time.Unix(c.ExpiresAt, 0).UTC().Format(time.RFC3339)
	}
	return gin.H{
		"connected":           c.AccessToken != "",
		"email":               c.Email,
		"uid":                 c.UID,
		"expires_at":          exp,
		"expires_within_lead": oauth.ExpiresWithinLead(c.ExpiresAt, lead),
		"fingerprint":         oauth.ClineFingerprint(c.AccessToken),
	}
}

// -- Cline ------------------------------------------------------------------

func (a *App) adminClineLoginURL(c *gin.Context) {
	var body struct {
		CallbackURL string `json:"callback_url"`
	}
	_ = c.ShouldBindJSON(&body)
	if body.CallbackURL == "" {
		body.CallbackURL = "https://app.cline.bot/callback"
	}
	c.JSON(http.StatusOK, gin.H{"url": oauth.ClineBuildAuthURL(body.CallbackURL)})
}

func (a *App) adminClineConnect(c *gin.Context) {
	var body struct {
		Code string `json:"code"`
	}
	if err := c.ShouldBindJSON(&body); err != nil || body.Code == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "code is required"})
		return
	}
	res, err := oauth.ClineExchangeCode(body.Code)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	a.storeClineCredential(c, res)
}

func (a *App) adminClineAutoConnect(c *gin.Context) {
	var body struct {
		Code         string `json:"code"`
		AccessToken  string `json:"access_token"`
		RefreshToken string `json:"refresh_token"`
		Email        string `json:"email"`
	}
	_ = c.ShouldBindJSON(&body)
	if body.Code != "" {
		res, err := oauth.ClineExchangeCode(body.Code)
		if err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
			return
		}
		a.storeClineCredential(c, res)
		return
	}
	if body.AccessToken == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "code or access_token is required"})
		return
	}
	a.storeClineCredential(c, map[string]any{
		"access_token": body.AccessToken, "refresh_token": body.RefreshToken, "email": body.Email,
	})
}

func (a *App) storeClineCredential(c *gin.Context, res map[string]any) {
	access, _ := res["access_token"].(string)
	refresh, _ := res["refresh_token"].(string)
	email, _ := res["email"].(string)
	var expires int64
	if s, _ := res["expires_at"].(string); s != "" {
		expires, _ = oauth.ParseISOTime(s)
	}
	cred := oauth.Credential{AccessToken: access, RefreshToken: refresh, Email: email, ExpiresAt: expires}
	p := a.oauthProvider("cline", "cline", oauth.ClineAPIBase)
	a.setOAuthKey(p, "oauth", oauth.Encode(oauth.ClinePrefix, cred))
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "cline.oauth.connect", "cline", email)
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"connected": true, "email": email})
}

func (a *App) adminClineStatus(c *gin.Context) {
	cred, ok := a.oauthCredential("cline", oauth.ClinePrefix)
	if !ok {
		c.JSON(http.StatusOK, gin.H{"connected": false})
		return
	}
	c.JSON(http.StatusOK, oauthStatus(cred, oauth.ClineRefreshLeadS))
}

func (a *App) adminClineRefresh(c *gin.Context) {
	cred, ok := a.oauthCredential("cline", oauth.ClinePrefix)
	if !ok || cred.RefreshToken == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "no refresh token; reconnect"})
		return
	}
	res, err := oauth.ClineRefresh(cred.RefreshToken)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error(), "relogin_required": true})
		return
	}
	if res == nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": "transient refresh failure"})
		return
	}
	cred.AccessToken, _ = res["access_token"].(string)
	if rt, _ := res["refresh_token"].(string); rt != "" {
		cred.RefreshToken = rt
	}
	if s, _ := res["expires_at"].(string); s != "" {
		cred.ExpiresAt, _ = oauth.ParseISOTime(s)
	}
	p := a.oauthProvider("cline", "cline", oauth.ClineAPIBase)
	a.setOAuthKey(p, "oauth", oauth.Encode(oauth.ClinePrefix, cred))
	_ = saveProvider(a.State.Store, *p)
	a.rebuild()
	c.JSON(http.StatusOK, oauthStatus(cred, oauth.ClineRefreshLeadS))
}

func (a *App) adminClineDisconnect(c *gin.Context) {
	p := a.findProvider("cline")
	if p != nil {
		a.removeOAuthKey(p, "oauth")
		_ = saveProvider(a.State.Store, *p)
	}
	a.State.Auth.Audit(a.actor(c), "cline.oauth.disconnect", "cline", "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"connected": false})
}

func (a *App) adminClineModels(c *gin.Context) {
	p := a.findProvider("cline")
	if p == nil {
		c.JSON(http.StatusOK, gin.H{"models": []any{}})
		return
	}
	models, err := a.listProviderModels(p)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": err.Error(), "models": []any{}})
		return
	}
	c.JSON(http.StatusOK, gin.H{"models": models})
}

// clineDefaultModels reads the persisted default-model list from the
// cline_settings row, accepting either spelling the console has used.
func (a *App) clineDefaultModels() []any {
	raw, ok, _ := a.State.Store.GetSetting("cline_settings")
	settings := map[string]any{}
	if ok && strings.TrimSpace(raw) != "" {
		_ = json.Unmarshal([]byte(raw), &settings)
	}
	for _, key := range []string{"default_models", "defaultModels"} {
		if list, ok := settings[key].([]any); ok {
			return list
		}
	}
	return []any{}
}

func (a *App) adminClineGetSettings(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"default_models": a.clineDefaultModels()})
}

func (a *App) adminClinePutSettings(c *gin.Context) {
	var body struct {
		DefaultModels []string `json:"default_models"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid JSON"})
		return
	}
	if body.DefaultModels == nil {
		body.DefaultModels = []string{}
	}
	b, _ := json.Marshal(map[string]any{"default_models": body.DefaultModels})
	_ = a.State.Store.SetSetting("cline_settings", string(b))
	c.JSON(http.StatusOK, gin.H{"default_models": body.DefaultModels})
}

func (a *App) adminClineDeleteDefaultModel(c *gin.Context) {
	modelID := c.Param("model_id")
	kept := []string{}
	for _, m := range a.clineDefaultModels() {
		if s, _ := m.(string); s == modelID {
			continue
		}
		if s, ok := m.(string); ok {
			kept = append(kept, s)
		}
	}
	b, _ := json.Marshal(map[string]any{"default_models": kept})
	_ = a.State.Store.SetSetting("cline_settings", string(b))
	c.JSON(http.StatusOK, gin.H{"default_models": kept})
}

// -- Hermes -----------------------------------------------------------------

func (a *App) adminHermesDeviceCode(c *gin.Context) {
	data, err := oauth.HermesRequestDeviceCode()
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, data)
}

func (a *App) adminHermesPoll(c *gin.Context) {
	var body struct {
		DeviceCode string `json:"device_code"`
	}
	if err := c.ShouldBindJSON(&body); err != nil || body.DeviceCode == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "device_code is required"})
		return
	}
	payload, err := oauth.HermesPollToken(body.DeviceCode)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	if payload == nil {
		c.JSON(http.StatusOK, gin.H{"pending": true})
		return
	}
	cred := oauth.HermesCredentialFromToken(payload)
	p := a.oauthProvider("hermes", "hermes", "https://inference-api.nousresearch.com/v1")
	a.setOAuthKey(p, "oauth", oauth.Encode(oauth.HermesPrefix, cred))
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "hermes.oauth.connect", "hermes", "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"connected": true})
}

func (a *App) adminHermesStatus(c *gin.Context) {
	cred, ok := a.oauthCredential("hermes", oauth.HermesPrefix)
	if !ok {
		c.JSON(http.StatusOK, gin.H{"connected": false})
		return
	}
	c.JSON(http.StatusOK, oauthStatus(cred, oauth.HermesRefreshLeadS))
}

func (a *App) adminHermesRefresh(c *gin.Context) {
	cred, ok := a.oauthCredential("hermes", oauth.HermesPrefix)
	if !ok || cred.RefreshToken == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "no refresh token; reconnect"})
		return
	}
	payload, err := oauth.HermesRefresh(cred.RefreshToken)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	cred = oauth.HermesCredentialFromToken(payload)
	p := a.oauthProvider("hermes", "hermes", "https://inference-api.nousresearch.com/v1")
	a.setOAuthKey(p, "oauth", oauth.Encode(oauth.HermesPrefix, cred))
	_ = saveProvider(a.State.Store, *p)
	a.rebuild()
	c.JSON(http.StatusOK, oauthStatus(cred, oauth.HermesRefreshLeadS))
}

func (a *App) adminHermesDisconnect(c *gin.Context) {
	p := a.findProvider("hermes")
	if p != nil {
		a.removeOAuthKey(p, "oauth")
		_ = saveProvider(a.State.Store, *p)
	}
	a.State.Auth.Audit(a.actor(c), "hermes.oauth.disconnect", "hermes", "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"connected": false})
}

// -- WorkBuddy --------------------------------------------------------------

func (a *App) workbuddyAccounts() []gin.H {
	out := []gin.H{}
	for _, p := range a.State.Cfg.Providers {
		if p.ProviderType != "workbuddy" {
			continue
		}
		for _, k := range p.Keys {
			cred, ok := oauth.Decode(oauth.WorkBuddyPrefix, k.Key)
			if !ok {
				continue
			}
			region := cred.Realm
			if region != "global" {
				region = "cn"
			}
			out = append(out, gin.H{
				"provider": "workbuddy", "label": k.Label,
				"status": "active", "enabled": k.Enabled, "valid_auth": true,
				"uid": cred.UID, "nickname": cred.Nickname,
				"domain": cred.Domain, "region": region,
				"expires_at":          cred.ExpiresAt,
				"needs_refresh":       oauth.ExpiresWithinLead(cred.ExpiresAt, oauth.WorkBuddyRefreshLeadS),
				"has_refresh_token":   cred.RefreshToken != "",
				"access_token_masked": maskSecret(cred.AccessToken),
			})
		}
	}
	return out
}

// maskSecret keeps a short prefix/suffix so an account row can be told apart
// without exposing the credential.
func maskSecret(secret string) string {
	if len(secret) <= 10 {
		return "••••"
	}
	return secret[:6] + "…" + secret[len(secret)-4:]
}

func (a *App) adminWorkBuddyAccounts(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"accounts": a.workbuddyAccounts()})
}

func (a *App) adminWorkBuddyImport(c *gin.Context) {
	var body struct {
		Accounts []struct {
			Label        string `json:"label"`
			AccessToken  string `json:"access_token"`
			RefreshToken string `json:"refresh_token"`
			Domain       string `json:"domain"`
			Realm        string `json:"realm"`
			UID          string `json:"uid"`
			Nickname     string `json:"nickname"`
		} `json:"accounts"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	p := a.oauthProvider("workbuddy", "workbuddy", oauth.WorkBuddyChatBaseCN)
	for i, acc := range body.Accounts {
		if acc.AccessToken == "" {
			continue
		}
		label := acc.Label
		if label == "" {
			label = fmt.Sprintf("account-%d", i+1)
		}
		cred := oauth.Credential{
			AccessToken: acc.AccessToken, RefreshToken: acc.RefreshToken,
			Domain: acc.Domain, Realm: acc.Realm, UID: acc.UID, Nickname: acc.Nickname,
		}
		a.setOAuthKey(p, label, oauth.Encode(oauth.WorkBuddyPrefix, cred))
	}
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "workbuddy.import", "workbuddy", "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"ok": true, "count": len(body.Accounts)})
}

func (a *App) adminWorkBuddyExport(c *gin.Context) {
	out := []gin.H{}
	for _, p := range a.State.Cfg.Providers {
		if p.ProviderType != "workbuddy" {
			continue
		}
		for _, k := range p.Keys {
			if cred, ok := oauth.Decode(oauth.WorkBuddyPrefix, k.Key); ok {
				out = append(out, gin.H{
					"label": k.Label, "access_token": cred.AccessToken,
					"refresh_token": cred.RefreshToken, "domain": cred.Domain,
					"realm": cred.Realm, "uid": cred.UID, "nickname": cred.Nickname,
				})
			}
		}
	}
	c.JSON(http.StatusOK, gin.H{"accounts": out})
}

func (a *App) adminWorkBuddyRefresh(c *gin.Context) {
	p := a.findProvider("workbuddy")
	if p == nil {
		c.JSON(http.StatusOK, gin.H{"refreshed": 0})
		return
	}
	refreshed := 0
	for i := range p.Keys {
		cred, ok := oauth.Decode(oauth.WorkBuddyPrefix, p.Keys[i].Key)
		if !ok {
			continue
		}
		nc, err := oauth.WorkBuddyRefresh(cred)
		if err != nil || nc == nil {
			continue
		}
		p.Keys[i].Key = oauth.Encode(oauth.WorkBuddyPrefix, *nc)
		refreshed++
	}
	_ = saveProvider(a.State.Store, *p)
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"refreshed": refreshed})
}

func (a *App) adminWorkBuddyLoginStart(c *gin.Context) {
	var body struct {
		Realm string `json:"realm"`
	}
	_ = c.ShouldBindJSON(&body)
	state, authURL, err := oauth.WorkBuddyStartLogin(body.Realm)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"state": state, "auth_url": authURL, "realm": body.Realm})
}

func (a *App) adminWorkBuddyLoginPoll(c *gin.Context) {
	var body struct {
		State string `json:"state"`
		Realm string `json:"realm"`
	}
	if err := c.ShouldBindJSON(&body); err != nil || body.State == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "state is required"})
		return
	}
	cred, err := oauth.WorkBuddyPollLogin(body.State, body.Realm)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": err.Error()})
		return
	}
	if cred == nil {
		c.JSON(http.StatusOK, gin.H{"pending": true})
		return
	}
	p := a.oauthProvider("workbuddy", "workbuddy", oauth.WorkBuddyChatBaseCN)
	label := cred.UID
	if label == "" {
		label = fmt.Sprintf("login-%d", time.Now().Unix())
	}
	a.setOAuthKey(p, label, oauth.Encode(oauth.WorkBuddyPrefix, *cred))
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "workbuddy.login", label, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"connected": true, "uid": cred.UID, "nickname": cred.Nickname})
}

// -- provider model listing -------------------------------------------------

func (a *App) adminProviderModels(c *gin.Context) {
	p := a.findProvider(c.Param("name"))
	if p == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "provider not found"})
		return
	}
	models, err := a.listProviderModels(p)
	if err != nil {
		c.JSON(http.StatusBadGateway, gin.H{"error": err.Error(), "models": []any{}})
		return
	}
	c.JSON(http.StatusOK, gin.H{"models": models})
}

func (a *App) listProviderModels(p *config.Provider) ([]any, error) {
	if len(p.Keys) == 0 {
		return nil, fmt.Errorf("provider has no keys")
	}
	secret := p.Keys[0].Key
	for _, prefix := range []string{oauth.ClinePrefix, oauth.WorkBuddyPrefix, oauth.HermesPrefix} {
		if cred, ok := oauth.Decode(prefix, secret); ok {
			secret = cred.AccessToken
			break
		}
	}
	base := strings.TrimRight(p.BaseURL, "/")
	var endpoint string
	headers := map[string]string{"Accept": "application/json"}
	switch p.ProviderType {
	case "anthropic":
		endpoint = base + "/v1/models"
		headers["x-api-key"] = secret
		headers["anthropic-version"] = "2023-06-01"
	case "gemini":
		endpoint = base + "/models"
		headers["x-goog-api-key"] = secret
	default:
		endpoint = base + "/models"
		headers["Authorization"] = "Bearer " + secret
	}
	req, _ := http.NewRequest("GET", endpoint, nil)
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("upstream HTTP %d: %s", resp.StatusCode, truncateStr(string(body), 200))
	}
	var payload map[string]any
	if json.Unmarshal(body, &payload) == nil {
		if data, ok := payload["data"].([]any); ok {
			return data, nil
		}
		if models, ok := payload["models"].([]any); ok {
			return models, nil
		}
	}
	var list []any
	if json.Unmarshal(body, &list) == nil {
		return list, nil
	}
	return []any{}, nil
}

func truncateStr(s string, n int) string {
	if len(s) > n {
		return s[:n]
	}
	return s
}
