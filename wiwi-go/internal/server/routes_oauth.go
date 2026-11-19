package server

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
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

// -- redirect OAuth session (Python's oauth_pending) -----------------------

type oauthSession struct {
	Kind       string
	Provider   string
	ReturnPath string
	CreatedAt  time.Time
	TTL        time.Duration
}

func randomStateToken() string {
	b := make([]byte, 24)
	_, _ = rand.Read(b)
	return base64.RawURLEncoding.EncodeToString(b)
}

func (a *App) evictExpiredOAuthPending() {
	now := time.Now()
	a.State.OAuthMu.Lock()
	for k, v := range a.State.OAuthPending {
		if now.Sub(v.CreatedAt) > v.TTL {
			delete(a.State.OAuthPending, k)
		}
	}
	a.State.OAuthMu.Unlock()
}

func (a *App) popOAuthSession(token string) (oauthSession, bool) {
	a.State.OAuthMu.Lock()
	s, ok := a.State.OAuthPending[token]
	delete(a.State.OAuthPending, token)
	a.State.OAuthMu.Unlock()
	if !ok || time.Since(s.CreatedAt) > s.TTL {
		return oauthSession{}, false
	}
	return s, true
}

// requestBase builds the absolute callback origin. public_url wins; otherwise
// the request Host is used. X-Forwarded-Host is deliberately ignored.
func (a *App) requestBase(c *gin.Context) string {
	if u := strings.TrimSpace(a.State.Cfg.Wiwi.PublicURL); u != "" {
		return strings.TrimRight(u, "/")
	}
	scheme := "http"
	if c.Request.TLS != nil || strings.EqualFold(c.GetHeader("X-Forwarded-Proto"), "https") {
		scheme = "https"
	}
	host := c.Request.Host
	if host == "" {
		host = "localhost"
	}
	return scheme + "://" + host
}

func clineCallbackRedirect(c *gin.Context, returnPath, provider, email, errMsg string) {
	q := url.Values{}
	if provider != "" {
		q.Set("cline_provider", provider)
	}
	if email != "" {
		q.Set("cline_connected", "1")
		q.Set("cline_email", email)
	}
	if errMsg != "" {
		q.Set("cline_error", errMsg)
	}
	sep := "?"
	if strings.Contains(returnPath, "?") {
		sep = "&"
	}
	c.Redirect(http.StatusFound, returnPath+sep+q.Encode())
}

func validClineReturnPath(p string) bool {
	return p == "/console" || strings.HasPrefix(p, "/console/")
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
	c.JSON(http.StatusOK, gin.H{"auth_url": oauth.ClineBuildAuthURL(body.CallbackURL)})
}

func maskToken(s string) string {
	if len(s) <= 8 {
		return "***"
	}
	return s[:4] + "…" + s[len(s)-4:]
}

func (a *App) adminClineConnect(c *gin.Context) {
	var body struct {
		Provider string `json:"provider"`
		Code     string `json:"code"`
	}
	if err := c.ShouldBindJSON(&body); err != nil || strings.TrimSpace(body.Code) == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "provider and code are required"})
		return
	}
	provider := strings.TrimSpace(body.Provider)
	legacyDefault := provider == ""
	if legacyDefault {
		provider = "cline"
	}
	p := a.findProvider(provider)
	if p == nil {
		if !legacyDefault {
			c.JSON(http.StatusNotFound, gin.H{"error": fmt.Sprintf("unknown provider %q", provider)})
			return
		}
		p = a.oauthProvider(provider, "cline", oauth.ClineAPIBase)
	}
	if len(p.Keys) == 0 && !legacyDefault {
		c.JSON(http.StatusBadRequest, gin.H{"error": fmt.Sprintf("provider %q has no keys — add a pool key first, then connect", provider)})
		return
	}
	res, err := oauth.ClineExchangeCode(body.Code)
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid Cline code: " + err.Error()})
		return
	}
	email := a.storeClineCredentialFor(c, provider, res)
	access, _ := res["access_token"].(string)
	c.JSON(http.StatusOK, gin.H{
		"provider": provider, "email": email, "access_token_masked": maskToken(access),
	})
}

// adminClineAutoConnect starts the redirect flow: it returns a Cline auth URL
// whose callback points at this gateway. The pending state token is single-use
// and carries the admin authorization; the public callback is not gated.
func (a *App) adminClineAutoConnect(c *gin.Context) {
	var body struct {
		Provider     string `json:"provider"`
		ReturnPath   string `json:"return_path"`
		Code         string `json:"code"`
		AccessToken  string `json:"access_token"`
		RefreshToken string `json:"refresh_token"`
		Email        string `json:"email"`
	}
	_ = c.ShouldBindJSON(&body)
	// Legacy direct-credential shape (Go-only before parity): keep it working.
	if body.Code != "" || body.AccessToken != "" {
		provider := strings.TrimSpace(body.Provider)
		if provider == "" {
			provider = "cline"
		}
		if body.Code != "" {
			res, err := oauth.ClineExchangeCode(body.Code)
			if err != nil {
				c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
				return
			}
			email := a.storeClineCredentialFor(c, provider, res)
			c.JSON(http.StatusOK, gin.H{"connected": true, "email": email})
			return
		}
		email := a.storeClineCredentialFor(c, provider, map[string]any{
			"access_token": body.AccessToken, "refresh_token": body.RefreshToken, "email": body.Email,
		})
		c.JSON(http.StatusOK, gin.H{"connected": true, "email": email})
		return
	}

	provider := strings.TrimSpace(body.Provider)
	returnPath := strings.TrimSpace(body.ReturnPath)
	if provider == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "provider is required"})
		return
	}
	if a.findProvider(provider) == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": fmt.Sprintf("unknown provider %q", provider)})
		return
	}
	if !validClineReturnPath(returnPath) {
		returnPath = "/console/oauth"
	}
	a.evictExpiredOAuthPending()
	token := randomStateToken()
	a.State.OAuthMu.Lock()
	a.State.OAuthPending[token] = oauthSession{
		Kind: "cline_redirect", Provider: provider, ReturnPath: returnPath,
		CreatedAt: time.Now(), TTL: 600 * time.Second,
	}
	a.State.OAuthMu.Unlock()
	cb := a.requestBase(c) + "/cline/oauth/callback?state=" + url.QueryEscape(token)
	c.JSON(http.StatusOK, gin.H{
		"auth_url": oauth.ClineBuildAuthURL(cb), "state": token, "provider": provider,
	})
}

// clineOAuthCallback is public: the admin auth happened when the pending
// session was created. The state token authorizes exactly one callback.
func (a *App) clineOAuthCallback(c *gin.Context) {
	code := strings.TrimSpace(c.Query("code"))
	token := strings.TrimSpace(c.Query("state"))
	if code == "" || token == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "missing code or state"})
		return
	}
	sess, ok := a.popOAuthSession(token)
	if !ok {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid or expired session"})
		return
	}
	provider := sess.Provider
	returnPath := sess.ReturnPath
	p := a.findProvider(provider)
	if p == nil {
		clineCallbackRedirect(c, returnPath, provider, "", "provider no longer exists")
		return
	}
	res, err := oauth.ClineExchangeCode(code)
	if err != nil {
		clineCallbackRedirect(c, returnPath, provider, "", "invalid code: "+err.Error())
		return
	}
	if len(p.Keys) == 0 {
		clineCallbackRedirect(c, returnPath, provider, "", "provider has no pool keys")
		return
	}
	email := a.storeClineCredentialFor(c, provider, res)
	clineCallbackRedirect(c, returnPath, provider, email, "")
}

func (a *App) storeClineCredential(c *gin.Context, res map[string]any) {
	a.storeClineCredentialFor(c, "cline", res)
}

func (a *App) storeClineCredentialFor(c *gin.Context, name string, res map[string]any) string {
	access, _ := res["access_token"].(string)
	refresh, _ := res["refresh_token"].(string)
	email, _ := res["email"].(string)
	var expires int64
	if s, _ := res["expires_at"].(string); s != "" {
		expires, _ = oauth.ParseISOTime(s)
	}
	cred := oauth.Credential{AccessToken: access, RefreshToken: refresh, Email: email, ExpiresAt: expires}
	p := a.findProvider(name)
	if p == nil {
		p = a.oauthProvider(name, "cline", oauth.ClineAPIBase)
	}
	a.setOAuthKey(p, "oauth", oauth.Encode(oauth.ClinePrefix, cred))
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "cline.oauth.connect", name, email)
	a.rebuild()
	return email
}

func (a *App) adminClineStatus(c *gin.Context) {
	explicit := strings.TrimSpace(c.Query("provider")) != ""
	provider := strings.TrimSpace(c.Query("provider"))
	if provider == "" {
		provider = "cline"
	}
	if a.findProvider(provider) == nil {
		if explicit {
			c.JSON(http.StatusNotFound, gin.H{"error": fmt.Sprintf("unknown provider %q", provider)})
			return
		}
		c.JSON(http.StatusOK, gin.H{"connected": false})
		return
	}
	cred, ok := a.oauthCredential(provider, oauth.ClinePrefix)
	if !ok {
		c.JSON(http.StatusOK, gin.H{"connected": false})
		return
	}
	c.JSON(http.StatusOK, oauthStatus(cred, oauth.ClineRefreshLeadS))
}

func (a *App) adminClineRefresh(c *gin.Context) {
	var body struct {
		Provider string `json:"provider"`
	}
	_ = c.ShouldBindJSON(&body)
	provider := strings.TrimSpace(body.Provider)
	if provider == "" {
		provider = strings.TrimSpace(c.Query("provider"))
	}
	if provider == "" {
		provider = "cline"
	}
	if a.findProvider(provider) == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": fmt.Sprintf("unknown provider %q", provider)})
		return
	}
	cred, ok := a.oauthCredential(provider, oauth.ClinePrefix)
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
	p := a.findProvider(provider)
	a.setOAuthKey(p, "oauth", oauth.Encode(oauth.ClinePrefix, cred))
	_ = saveProvider(a.State.Store, *p)
	a.rebuild()
	c.JSON(http.StatusOK, oauthStatus(cred, oauth.ClineRefreshLeadS))
}

func (a *App) adminClineDisconnect(c *gin.Context) {
	var body struct {
		Provider string `json:"provider"`
	}
	_ = c.ShouldBindJSON(&body)
	explicit := strings.TrimSpace(body.Provider) != "" || strings.TrimSpace(c.Query("provider")) != ""
	provider := strings.TrimSpace(body.Provider)
	if provider == "" {
		provider = strings.TrimSpace(c.Query("provider"))
	}
	if provider == "" {
		provider = "cline"
	}
	p := a.findProvider(provider)
	if p == nil {
		if explicit {
			c.JSON(http.StatusNotFound, gin.H{"error": fmt.Sprintf("unknown provider %q", provider)})
			return
		}
		c.JSON(http.StatusOK, gin.H{"provider": provider, "disconnected": true})
		return
	}
	a.removeOAuthKey(p, "oauth")
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "cline.oauth.disconnect", provider, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"provider": provider, "disconnected": true})
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
