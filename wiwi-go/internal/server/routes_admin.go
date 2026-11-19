package server

import (
	"encoding/json"
	"math"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"wiwi-go/internal/auth"
	"wiwi-go/internal/config"
	"wiwi-go/internal/cost"
	"wiwi-go/internal/logging"
	"wiwi-go/internal/router"
)

func (a *App) registerAdminRoutes(g *gin.RouterGroup) {
	// virtual keys
	g.POST("/keys/generate", a.adminGenerateKey)
	g.GET("/keys", a.adminListKeys)
	g.PATCH("/keys/:id", a.adminPatchKey)
	g.DELETE("/keys/:id", a.adminDeleteKey)
	g.POST("/keys/:id/disable", a.adminDisableKey)

	// users
	g.GET("/users", a.adminListUsers)
	g.PATCH("/users/:uid", a.adminPatchUser)

	// providers / models
	g.GET("/provider-catalog", a.adminProviderCatalog)
	g.GET("/providers", a.adminListProviders)
	g.GET("/models", a.adminModels)
	g.PATCH("/model-groups/:name", a.adminPatchModelGroup)
	g.GET("/aliases", a.adminListAliases)
	g.POST("/aliases", a.adminAddAlias)

	// provider + key-pool + deployment mutations (persisted to the DB overlay)
	g.POST("/providers", a.adminCreateProvider)
	g.PATCH("/providers/:name", a.adminPatchProvider)
	g.DELETE("/providers/:name", a.adminDeleteProvider)
	g.POST("/providers/:name/keys", a.adminAddProviderKey)
	g.PATCH("/providers/:name/keys/:label", a.adminPatchProviderKey)
	g.DELETE("/providers/:name/keys/:label", a.adminDeleteProviderKey)
	g.GET("/providers/:name/keys/:label/secret", a.adminRevealProviderKey)
	g.POST("/model-groups/:name/deployments", a.adminAddDeployment)
	g.DELETE("/model-groups/:name/deployments", a.adminDeleteDeployment)
	g.POST("/model-groups/:name/deployments/delete", a.adminDeleteDeployment)
	g.GET("/providers/export", a.adminExportProviders)
	g.POST("/providers/import", a.adminImportProviders)

	// pricing
	g.GET("/pricing", a.adminListPricing)
	g.PUT("/pricing/*id", a.adminUpsertPricing)
	g.DELETE("/pricing/*id", a.adminDeletePricing)

	// logs & stats
	g.GET("/logs/requests", a.adminLogsRequests)
	g.GET("/logs/audit", a.adminLogsAudit)
	g.GET("/logs/proxy", a.adminLogsProxy)
	g.GET("/logs/requests/:id/metrics", a.adminRequestMetrics)
	g.GET("/stream", a.adminStream)
	g.GET("/stats/overview", a.adminStatsOverview)
	g.GET("/stats/timeseries", a.adminStatsTimeseries)

	// backup
	g.GET("/backup", a.adminBackup)
	g.POST("/backup/import", a.adminBackupImport)

	// alert rules (stored as settings)
	g.GET("/alert-rules", a.adminGetAlertRules)
	g.PUT("/alert-rules", a.adminPutAlertRules)

	// OAuth + account surfaces
	g.POST("/cline/oauth/login-url", a.adminClineLoginURL)
	g.POST("/cline/oauth/connect", a.adminClineConnect)
	g.POST("/cline/oauth/auto-connect", a.adminClineAutoConnect)
	g.GET("/cline/oauth/status", a.adminClineStatus)
	g.POST("/cline/oauth/refresh", a.adminClineRefresh)
	g.DELETE("/cline/oauth/disconnect", a.adminClineDisconnect)
	g.GET("/cline/models", a.adminClineModels)
	g.GET("/cline/settings", a.adminClineGetSettings)
	g.PUT("/cline/settings", a.adminClinePutSettings)
	g.DELETE("/cline/settings/default-models/:model_id", a.adminClineDeleteDefaultModel)

	g.POST("/hermes/oauth/device-code", a.adminHermesDeviceCode)
	g.POST("/hermes/oauth/poll", a.adminHermesPoll)
	g.GET("/hermes/oauth/status", a.adminHermesStatus)
	g.POST("/hermes/oauth/refresh", a.adminHermesRefresh)
	g.DELETE("/hermes/oauth/disconnect", a.adminHermesDisconnect)

	g.GET("/workbuddy/accounts", a.adminWorkBuddyAccounts)
	g.POST("/workbuddy/import", a.adminWorkBuddyImport)
	g.GET("/workbuddy/export", a.adminWorkBuddyExport)
	g.POST("/workbuddy/refresh", a.adminWorkBuddyRefresh)
	g.POST("/workbuddy/login/start", a.adminWorkBuddyLoginStart)
	g.POST("/workbuddy/login/poll", a.adminWorkBuddyLoginPoll)
	g.GET("/workbuddy/status", a.adminWorkBuddyAccounts)

	g.GET("/providers/:name/models", a.adminProviderModels)
}

func (a *App) actor(c *gin.Context) string {
	ai := authOf(c)
	if ai == nil {
		return "admin"
	}
	return ai.KeyID
}

func (a *App) adminGenerateKey(c *gin.Context) {
	var body struct {
		Name      string   `json:"name"`
		MaxBudget *float64 `json:"max_budget"`
		RPM       *int     `json:"rpm"`
		TPM       *int     `json:"tpm"`
		Models    []string `json:"models"`
		TTL       int64    `json:"ttl_s"`
		TTLWeb    int64    `json:"ttl_seconds"`
	}
	_ = c.ShouldBindJSON(&body)
	ttl := body.TTL
	if ttl == 0 {
		ttl = body.TTLWeb
	}
	owner := ""
	if ai := authOf(c); ai != nil && !ai.IsMaster {
		owner = ai.UserID
	}
	plain, v, err := a.State.Auth.GenerateOwned(owner, body.Name, body.MaxBudget, body.RPM, body.TPM, body.Models, ttl, a.State.Cfg.General.MaxKeysPerUser)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "key.generate", v.ID, "")
	c.JSON(http.StatusOK, gin.H{"key": plain, "id": v.ID, "note": ""})
}

func (a *App) adminListKeys(c *gin.Context) {
	keys, err := a.State.Auth.List()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	out := make([]map[string]any, 0, len(keys))
	for _, k := range keys {
		out = append(out, consoleKey(k))
	}
	c.JSON(http.StatusOK, gin.H{"keys": out})
}

func (a *App) adminPatchKey(c *gin.Context) {
	id := c.Param("id")
	raw, err := c.GetRawData()
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	fields := map[string]json.RawMessage{}
	if err := json.Unmarshal(raw, &fields); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body"})
		return
	}
	if err := a.State.Auth.PatchConsole(id, fields); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "key.patch", id, "")
	updated, err := a.State.Auth.Get(id)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"key": consoleKey(updated)})
}

func (a *App) adminDeleteKey(c *gin.Context) {
	if err := a.State.Auth.Delete(c.Param("id")); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "key.delete", c.Param("id"), "")
	c.JSON(http.StatusOK, gin.H{"deleted": true})
}

func (a *App) adminDisableKey(c *gin.Context) {
	var body struct {
		Disabled *bool `json:"disabled"`
	}
	_ = c.ShouldBindJSON(&body)
	disabled := true
	if body.Disabled != nil {
		disabled = *body.Disabled
	}
	if err := a.State.Auth.SetEnabled(c.Param("id"), !disabled); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "key.disable", c.Param("id"), "")
	c.JSON(http.StatusOK, gin.H{"key_id": c.Param("id"), "disabled": disabled})
}

func (a *App) adminListUsers(c *gin.Context) {
	users, err := a.State.Auth.ListUsers()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	out := make([]map[string]any, 0, len(users))
	for _, u := range users {
		out = append(out, consoleUser(u))
	}
	c.JSON(http.StatusOK, gin.H{"users": out})
}

func (a *App) adminPatchUser(c *gin.Context) {
	var body struct {
		Role     *string `json:"role"`
		Disabled *bool   `json:"disabled"`
	}
	_ = c.ShouldBindJSON(&body)
	id := c.Param("uid")
	if body.Role != nil {
		if err := a.State.Auth.SetUserRole(id, *body.Role); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
	}
	if body.Disabled != nil {
		if err := a.State.Auth.SetUserDisabled(id, *body.Disabled); err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
	}
	a.State.Auth.Audit(a.actor(c), "user.patch", id, "")
	u, err := a.State.Auth.GetUser(id)
	if err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "user not found"})
		return
	}
	c.JSON(http.StatusOK, consoleUser(u))
}

func (a *App) adminProviderCatalog(c *gin.Context) {
	c.JSON(http.StatusOK, a.providerCatalog())
}

func (a *App) adminListProviders(c *gin.Context) {
	now := time.Now()
	providers := a.State.Router.Providers()
	names := make([]string, 0, len(providers))
	for name := range providers {
		names = append(names, name)
	}
	sort.Strings(names)
	out := []map[string]any{}
	for _, name := range names {
		p := providers[name]
		keys := make([]map[string]any, 0, len(p.Keys))
		healthy := len(p.Keys) == 0
		for _, k := range p.Keys {
			if k.Available(now) {
				healthy = true
			}
			cool := 0.0
			if k.State == "cooling" && now.Before(k.Until) {
				cool = k.Until.Sub(now).Seconds()
			}
			status := k.State
			if status == "" {
				status = "active"
			}
			if !k.Enabled {
				status = "disabled"
			}
			masked := ""
			if k.Secret != "" {
				masked = "••••"
			}
			keys = append(keys, map[string]any{
				"label": k.Label, "masked": masked, "weight": k.Weight,
				"enabled": k.Enabled, "status": status, "cooldown_remaining_s": cool,
				"req_count": 0, "err_count": k.ErrCount, "last_used_ts": nil,
			})
		}
		var aliasID any
		if p.AliasID != "" {
			aliasID = p.AliasID
		}
		out = append(out, map[string]any{
			"name": name, "provider_type": p.Type, "base_url": p.BaseURL,
			"round_robin": p.RoundRobin, "alias_id": aliasID, "healthy": healthy,
			"keys": keys,
		})
	}
	c.JSON(http.StatusOK, gin.H{"providers": out, "alias_to_provider": a.consoleProviderAliases()})
}

func (a *App) adminModels(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"groups":           a.consoleModelGroups(),
		"aliases":          a.consoleAliases(),
		"provider_aliases": a.consoleProviderAliases(),
		"strategy":         a.State.Router.Strategy(),
	})
}

func (a *App) adminPatchModelGroup(c *gin.Context) {
	group := a.State.Router.ResolveGroup(c.Param("name"))
	deps := a.State.Router.Deployments(group)
	if len(deps) == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "unknown model group"})
		return
	}
	var body struct {
		Weights  map[string]int `json:"weights"`
		Strategy *string        `json:"strategy"`
	}
	_ = c.ShouldBindJSON(&body)
	byIdent := map[string]*router.Deployment{}
	for _, d := range deps {
		byIdent[d.Provider.Name+"/"+d.Model] = d
	}
	for ident, w := range body.Weights {
		d, ok := byIdent[ident]
		if !ok {
			c.JSON(http.StatusBadRequest, gin.H{"error": "unknown deployment: " + ident})
			return
		}
		if w < 1 {
			w = 1
		}
		d.Weight = w
		for i := range a.State.Cfg.ModelList {
			m := &a.State.Cfg.ModelList[i]
			if m.ModelName == group && m.Params.Provider == d.Provider.Name && m.Params.Model == d.Model {
				m.Params.Weight = w
				_ = saveDeployment(a.State.Store, group+"|"+d.Provider.Name+"|"+d.Model, group, m.Params)
			}
		}
	}
	if body.Strategy != nil {
		s := *body.Strategy
		if s != "simple-shuffle" && s != "least-busy" && s != "latency-based" {
			c.JSON(http.StatusBadRequest, gin.H{"error": "invalid strategy '" + s + "'"})
			return
		}
		a.State.Router.SetStrategy(s)
		_ = a.State.Store.SetSetting("routing_strategy", s)
	}
	a.State.Auth.Audit(a.actor(c), "model_group.update", group, "")
	c.JSON(http.StatusOK, gin.H{"group": group})
}

func (a *App) adminListAliases(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"aliases": a.consoleAliases()})
}

// dbPrices reads the admin-owned price table (not the bundled defaults), which
// is what the console lists and what a backup carries — matching Python's
// config_store-backed /admin/pricing.
func (a *App) dbPrices() []map[string]any {
	rows := a.exportTable(`SELECT model,prompt,completion,cached FROM model_prices ORDER BY model`,
		[]string{"model", "prompt", "completion", "cached"})
	out := make([]map[string]any, 0, len(rows))
	for _, r := range rows {
		out = append(out, consolePrice(mapStr(r, "model"),
			numOr(r["prompt"], 0), numOr(r["completion"], 0), numOr(r["cached"], 0)))
	}
	return out
}

func (a *App) adminListPricing(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"models": a.dbPrices()})
}

// pricingModelID returns the model id from the wildcard route segment. The
// console percent-encodes ids that contain slashes, so the raw segment may
// arrive escaped; gin unescapes it before we see it.
func pricingModelID(c *gin.Context) string {
	id := strings.TrimPrefix(c.Param("id"), "/")
	if id == "" {
		id = c.Param("id")
	}
	return id
}

func (a *App) adminUpsertPricing(c *gin.Context) {
	var body struct {
		InputPer1M         *float64 `json:"input_per_1m"`
		OutputPer1M        *float64 `json:"output_per_1m"`
		CacheReadPer1M     *float64 `json:"cache_read_per_1m"`
		CacheCreationPer1M *float64 `json:"cache_creation_per_1m"`
		// Legacy per-token spellings (the pre-console Go API).
		Input  *float64 `json:"input"`
		Output *float64 `json:"output"`
		Cached *float64 `json:"cached"`
	}
	_ = c.ShouldBindJSON(&body)
	model := pricingModelID(c)
	price := cost.ModelPrice{}
	if body.Input != nil {
		price.Prompt = *body.Input
	}
	if body.Output != nil {
		price.Completion = *body.Output
	}
	if body.Cached != nil {
		price.Cached = *body.Cached
	}
	if body.InputPer1M != nil {
		price.Prompt = *body.InputPer1M / 1e6
	}
	if body.OutputPer1M != nil {
		price.Completion = *body.OutputPer1M / 1e6
	}
	if body.CacheReadPer1M != nil {
		price.Cached = *body.CacheReadPer1M / 1e6
	}
	a.State.Cost.Upsert(model, price)
	_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
		`INSERT INTO model_prices(model,prompt,completion,cached) VALUES(?,?,?,?)
		 ON CONFLICT(model) DO UPDATE SET prompt=excluded.prompt,completion=excluded.completion,cached=excluded.cached`),
		model, price.Prompt, price.Completion, price.Cached)
	a.State.Auth.Audit(a.actor(c), "pricing.upsert", model, "")
	c.JSON(http.StatusOK, consolePrice(model, price.Prompt, price.Completion, price.Cached))
}

func (a *App) adminDeletePricing(c *gin.Context) {
	model := pricingModelID(c)
	a.State.Cost.Delete(model)
	_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(`DELETE FROM model_prices WHERE model=?`), model)
	a.State.Auth.Audit(a.actor(c), "pricing.delete", model, "")
	c.JSON(http.StatusOK, gin.H{"deleted": true, "model_id": model})
}

func (a *App) adminLogsRequests(c *gin.Context) {
	// Same contract as Python: limit is capped at 50k (a safety net, not a
	// product limit), minutes==0 means all-time, and offset pages the same
	// ts-DESC / id-DESC ordering.
	limit := queryInt(c, "limit", 10000)
	if limit < 1 {
		limit = 1
	}
	if limit > 50000 {
		limit = 50000
	}
	minutes := queryInt(c, "minutes", 0)
	if minutes < 0 {
		minutes = 0
	}
	offset := queryInt(c, "offset", 0)
	if offset < 0 {
		offset = 0
	}
	where := ""
	var args []any
	if minutes > 0 {
		where = " WHERE ts >= ?"
		args = append(args, time.Now().Unix()-int64(minutes)*60)
	}
	args = append(args, limit, offset)
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT id,request_id,ts,key_id,model_group,provider,model_id,status,prompt_tokens,
		 completion_tokens,cached_tokens,reasoning_tokens,cost,latency_ms,ttft_ms,tps,error
		 FROM request_logs`+where+` ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?`), args...)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()
	c.Header("Cache-Control", "no-store")
	out := []map[string]any{}
	for rows.Next() {
		var id, rid, keyID, group, prov, model, errText string
		var ts int64
		var status, pt, ct, cached, reasoning, lat, ttft int
		var costv, tps float64
		if err := rows.Scan(&id, &rid, &ts, &keyID, &group, &prov, &model, &status,
			&pt, &ct, &cached, &reasoning, &costv, &lat, &ttft, &tps, &errText); err != nil {
			continue
		}
		out = append(out, map[string]any{
			"stream": "request", "id": id, "request_id": rid, "ts": ts,
			"key_id": keyID, "key_alias": "", "surface": "",
			"model_group": group, "provider": prov, "model_id": model,
			"provider_key_label": "", "status": status,
			"error_code": "", "error_message": errText,
			"tok_in": pt, "tok_cached": cached, "tok_cache_creation": 0,
			"tok_reasoning": reasoning, "tok_out": ct,
			"usage_estimated": false, "tps": tps,
			"ttft_ms": ttft, "latency_ms": lat, "cost": costv,
			"was_stream": false, "cache_hit": cached > 0, "cache_savings": 0.0,
			"response_cache_hit":   false,
			"translation_warnings": []string{}, "attempts": []any{},
			"request_body": nil,
		})
	}
	c.JSON(http.StatusOK, gin.H{"logs": out})
}

func (a *App) adminLogsAudit(c *gin.Context) {
	limit := queryInt(c, "limit", 200)
	if limit < 1 {
		limit = 1
	}
	if limit > 5000 {
		limit = 5000
	}
	c.Header("Cache-Control", "no-store")
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT id,ts,actor,action,target,diff FROM audit_logs ORDER BY ts DESC, id DESC LIMIT ?`), limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id, actor, action, target, diff string
		var ts int64
		if rows.Scan(&id, &ts, &actor, &action, &target, &diff) != nil {
			continue
		}
		out = append(out, map[string]any{
			"stream": "audit", "ts": ts, "actor": actor,
			"action": action, "target": target, "diff": decodeJSONObject(diff),
		})
	}
	c.JSON(http.StatusOK, gin.H{"logs": out})
}

func (a *App) adminLogsProxy(c *gin.Context) {
	events := a.State.Bus.EventsSince(queryInt64(c, "since", 0))
	// Python serves the newest 500 ring entries, newest-first. Keep that
	// contract: the console renders the array as it arrives.
	if len(events) > 500 {
		events = events[len(events)-500:]
	}
	out := make([]map[string]any, 0, len(events))
	for i := len(events) - 1; i >= 0; i-- {
		out = append(out, consoleProxyEntry(events[i]))
	}
	c.Header("Cache-Control", "no-store")
	c.JSON(http.StatusOK, gin.H{"logs": out})
}

func (a *App) adminRequestMetrics(c *gin.Context) {
	row := a.State.Store.QueryRow(a.State.Store.Rebind(
		`SELECT status,prompt_tokens,completion_tokens,cost,latency_ms,ttft_ms,attempts
		 FROM request_logs WHERE request_id=? OR id=? LIMIT 1`), c.Param("id"), c.Param("id"))
	var status, pt, ct, lat, ttft int
	var costv float64
	var attempts string
	if err := row.Scan(&status, &pt, &ct, &costv, &lat, &ttft, &attempts); err != nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
		return
	}
	var att []map[string]any
	_ = json.Unmarshal([]byte(attempts), &att)
	c.JSON(http.StatusOK, gin.H{
		"status": status, "prompt_tokens": pt, "completion_tokens": ct, "cost": costv,
		"latency_ms": lat, "ttft_ms": ttft, "attempts": att,
	})
}

func (a *App) adminStream(c *gin.Context) {
	since := queryInt64(c, "since", 0)
	if h := c.GetHeader("Last-Event-ID"); h != "" {
		if n, err := strconv.ParseInt(h, 10, 64); err == nil {
			since = n
		}
	}
	ch, cancel := a.State.Bus.Subscribe(since)
	defer cancel()
	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("Connection", "keep-alive")
	flusher, _ := c.Writer.(http.Flusher)
	// Flush headers (and an initial SSE comment) immediately. The console's
	// fetch-based client marks the stream connected as soon as the response
	// resolves; without an early write gin buffers the headers until the first
	// event, which may be minutes away, leaving the badge stuck on "OFFLINE".
	if flusher != nil {
		c.Writer.Write([]byte(":ok\n\n"))
		flusher.Flush()
	}
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	timeout := time.After(30 * time.Minute)
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				return
			}
			// Map the Go bus types onto the SSE event names the console
			// subscribes to. The payload is the entry itself (not the bus
			// envelope), matching Python's public_dict shape. A request event
			// feeds both the request-log tail and the proxy-log tail, exactly
			// as Python's proxy pipeline emits both.
			emit := func(name string, payload any) {
				b, _ := json.Marshal(payload)
				c.Writer.Write([]byte("id: " + strconv.FormatInt(ev.ID, 10) + "\n"))
				c.Writer.Write([]byte("event: " + name + "\n"))
				c.Writer.Write([]byte("data: " + string(b) + "\n\n"))
			}
			switch ev.Type {
			case "request":
				emit("log.created", ev.Data)
				emit("proxy.log", consoleProxyEntry(ev))
			case "alert", "unpriced":
				emit("proxy.log", consoleProxyEntry(ev))
			default:
				emit(ev.Type, ev.Data)
			}
			if flusher != nil {
				flusher.Flush()
			}
		case <-ticker.C:
			// SSE comment keep-alive: stops idle proxies from reaping the
			// connection and confirms liveness to the client.
			if _, err := c.Writer.Write([]byte(":ping\n\n")); err != nil {
				return
			}
			if flusher != nil {
				flusher.Flush()
			}
		case <-timeout:
			return
		case <-c.Request.Context().Done():
			return
		}
	}
}

func (a *App) adminStatsOverview(c *gin.Context) {
	minutes := queryInt(c, "minutes", 60)
	if minutes < 0 {
		minutes = 0
	}
	if minutes > 43200 {
		minutes = 43200
	}
	now := time.Now().Unix()
	hasWindow := minutes > 0
	cutoff := int64(0)
	where := ""
	var args []any
	if hasWindow {
		cutoff = now - int64(minutes)*60
		where = " WHERE ts >= ?"
		args = append(args, cutoff)
	}
	var requests, errors, cacheHits, tokIn, tokCached, tokReasoning, tokOut int64
	var tpsSamples, tpsCount int64
	var tpsSum, cost float64
	_ = a.State.Store.QueryRow(a.State.Store.Rebind(
		`SELECT COUNT(*),
		        COALESCE(SUM(CASE WHEN status >= 400 OR (error IS NOT NULL AND error != '') THEN 1 ELSE 0 END),0),
		        COALESCE(SUM(prompt_tokens),0),
		        COALESCE(SUM(cached_tokens),0),
		        COALESCE(SUM(reasoning_tokens),0),
		        COALESCE(SUM(completion_tokens),0),
		        COALESCE(SUM(CASE WHEN cached_tokens > 0 THEN 1 ELSE 0 END),0),
		        COALESCE(SUM(cost),0),
		        COALESCE(SUM(tps),0),
		        COALESCE(SUM(CASE WHEN tps > 0 THEN 1 ELSE 0 END),0)
		 FROM request_logs`+where), args...).
		Scan(&requests, &errors, &tokIn, &tokCached, &tokReasoning, &tokOut,
			&cacheHits, &cost, &tpsSum, &tpsCount)

	// Rows already rolled up and deleted still count: without this the
	// dashboard silently shrinks every time retention prunes.
	roll := a.rollupTotals(hasWindow, cutoff)
	requests += roll.requests
	errors += roll.errors
	tokIn += roll.tokIn
	tokOut += roll.tokOut
	cost += roll.cost
	tpsSum += roll.tpsSum
	tpsCount += roll.tpsCount
	tpsSamples = tpsCount // exact coverage, not the capped sample length
	hist := a.rollupHists(hasWindow, cutoff)

	tpsAvg := 0.0
	if tpsCount > 0 {
		tpsAvg = roundTo(tpsSum/float64(tpsCount), 2)
	}
	rpm := 0.0
	if hasWindow {
		rpm = roundTo(float64(requests)/float64(minutes), 2)
	}
	c.JSON(http.StatusOK, gin.H{
		"window_minutes":      minutes,
		"generated_at":        float64(now),
		"requests":            requests,
		"errors":              errors,
		"error_rate":          roundTo(safeDiv(float64(errors), float64(requests)), 4),
		"requests_per_minute": rpm,
		"tok_in":              tokIn,
		"tok_cached":          tokCached,
		"tok_cache_creation":  0,
		"tok_reasoning":       tokReasoning,
		"tok_out":             tokOut,
		"estimated_requests":  0,
		"cache_hits":          cacheHits,
		"cache_hit_rate":      roundTo(safeDiv(float64(cacheHits), float64(requests)), 4),
		"tps_avg":             tpsAvg,
		"tps_p95":             roundTo(logging.PercentileWithSamples(a.sampleColumn("tps", hasWindow, cutoff), hist["tps"]), 2),
		"tps_sample_ratio":    roundTo(safeDiv(float64(tpsSamples), float64(requests)), 4),
		"ttft_p95_ms":         roundTo(logging.PercentileWithSamples(a.sampleColumn("ttft_ms", hasWindow, cutoff), hist["ttft_ms"]), 1),
		"latency_p95_ms":      roundTo(logging.PercentileWithSamples(a.sampleColumn("latency_ms", hasWindow, cutoff), hist["latency_ms"]), 1),
		"cost":                roundTo(cost, 6),
		"cache_savings":       0.0,
	})
}

type rollupSum struct {
	requests, errors, tokIn, tokOut, tpsCount int64
	cost, tpsSum                              float64
}

// rollupTotals aggregates request_rollups over the same window as the raw
// read. Buckets are hour-floored, so the cutoff compares on bucket start.
func (a *App) rollupTotals(hasWindow bool, cutoff int64) rollupSum {
	q := `SELECT COALESCE(SUM(requests),0), COALESCE(SUM(errors),0),
	             COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0),
	             COALESCE(SUM(cost),0), COALESCE(SUM(tps_sum),0),
	             COALESCE(SUM(tps_count),0)
	      FROM request_rollups`
	var args []any
	if hasWindow {
		q += " WHERE bucket >= ?"
		args = append(args, cutoff)
	}
	var out rollupSum
	_ = a.State.Store.QueryRow(a.State.Store.Rebind(q), args...).
		Scan(&out.requests, &out.errors, &out.tokIn, &out.tokOut, &out.cost,
			&out.tpsSum, &out.tpsCount)
	return out
}

// rollupHists merges every stored p95_hist in the window by series, so raw
// samples can be unioned with the rolled-up distribution.
func (a *App) rollupHists(hasWindow bool, cutoff int64) map[string]map[int]int {
	q := `SELECT p95_hist FROM request_rollups WHERE p95_hist != ''`
	var args []any
	if hasWindow {
		q += " AND bucket >= ?"
		args = append(args, cutoff)
	}
	rows, err := a.State.Store.Query(a.State.Store.Rebind(q), args...)
	if err != nil {
		return map[string]map[int]int{}
	}
	defer rows.Close()
	out := map[string]map[int]int{}
	for rows.Next() {
		var text string
		if rows.Scan(&text) != nil {
			continue
		}
		for name, h := range logging.DecodeHists(text) {
			out[name] = logging.MergeHists(out[name], h)
		}
	}
	return out
}

// sampleColumn loads up to 5000 positive values of col in the window (newest
// first), the same bounded sample the Python DB sink reads for its percentiles.
func (a *App) sampleColumn(col string, hasWindow bool, cutoff int64) []float64 {
	q := "SELECT " + col + " FROM request_logs WHERE " + col + " > 0"
	var args []any
	if hasWindow {
		q += " AND ts >= ?"
		args = append(args, cutoff)
	}
	q += " ORDER BY ts DESC LIMIT 5000"
	rows, err := a.State.Store.Query(a.State.Store.Rebind(q), args...)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var out []float64
	for rows.Next() {
		var v float64
		if rows.Scan(&v) == nil {
			out = append(out, v)
		}
	}
	return out
}

// bucketSizeFor mirrors stats.bucket_size_for: the concrete bucket width for a
// window length, so the Go grid matches the Python one.
func bucketSizeFor(minutes int) int64 {
	switch {
	case minutes == 0:
		return 86400
	case minutes <= 1440:
		return 60
	case minutes <= 10080:
		return 3600
	case minutes <= 43200:
		return 21600
	default:
		return 86400
	}
}

// percentile95 is the nearest-rank p95 (0.0 when empty), matching
// wiwi/server/stats.py percentile().
func percentile95(vals []float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	sort.Float64s(vals)
	idx := int(math.Ceil(float64(len(vals))*0.95)) - 1
	if idx < 0 {
		idx = 0
	}
	if idx >= len(vals) {
		idx = len(vals) - 1
	}
	return vals[idx]
}

func safeDiv(a, b float64) float64 {
	if b == 0 {
		return 0
	}
	return a / b
}

func roundTo(v float64, places int) float64 {
	p := math.Pow(10, float64(places))
	return math.Round(v*p) / p
}

func (a *App) adminStatsTimeseries(c *gin.Context) {
	minutes := queryInt(c, "minutes", 60)
	if minutes < 0 {
		minutes = 0
	}
	if minutes > 43200 {
		minutes = 43200
	}
	metric := c.Query("metric")
	if metric == "" {
		metric = "tokens"
	}
	if metric != "tokens" && metric != "tps" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "unsupported metric"})
		return
	}
	size := bucketSizeFor(minutes)
	now := time.Now().Unix()
	hasWindow := minutes > 0
	cutoff := int64(0)
	q := `SELECT (ts/?) AS bucket,
	             COALESCE(SUM(prompt_tokens),0),
	             COALESCE(SUM(cached_tokens),0),
	             COALESCE(SUM(reasoning_tokens),0),
	             COALESCE(SUM(completion_tokens),0)
	      FROM request_logs`
	args := []any{size}
	if hasWindow {
		cutoff = now - int64(minutes)*60
		q += " WHERE ts >= ?"
		args = append(args, cutoff)
	}
	q += " GROUP BY bucket ORDER BY bucket"
	rows, err := a.State.Store.Query(a.State.Store.Rebind(q), args...)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	type row struct{ bucket, tokIn, tokCached, tokReasoning, tokOut int64 }
	var data []row
	var minB, maxB int64
	first := true
	for rows.Next() {
		var r row
		if rows.Scan(&r.bucket, &r.tokIn, &r.tokCached, &r.tokReasoning, &r.tokOut) != nil {
			continue
		}
		data = append(data, r)
		if first || r.bucket < minB {
			minB = r.bucket
		}
		if first || r.bucket > maxB {
			maxB = r.bucket
		}
		first = false
	}
	rows.Close()

	var firstT int64
	var nBuckets int
	if hasWindow {
		nBuckets = int(int64(minutes) * 60 / size)
		if nBuckets < 1 {
			nBuckets = 1
		}
		firstT = (now/size)*size - int64(nBuckets-1)*size
	} else if len(data) == 0 {
		nBuckets = 0
		firstT = (now / size) * size
	} else {
		nBuckets = int((maxB-minB)/size) + 1
		firstT = minB
	}

	tokIn := make([]int64, nBuckets)
	tokCached := make([]int64, nBuckets)
	tokReasoning := make([]int64, nBuckets)
	tokOut := make([]int64, nBuckets)
	for _, r := range data {
		if nBuckets == 0 {
			break
		}
		idx := int((r.bucket - firstT) / size)
		if idx < 0 {
			idx = 0
		} else if idx >= nBuckets {
			idx = nBuckets - 1
		}
		tokIn[idx] += r.tokIn
		tokCached[idx] += r.tokCached
		tokReasoning[idx] += r.tokReasoning
		tokOut[idx] += r.tokOut
	}

	buckets := make([]map[string]any, 0, nBuckets)
	for i := 0; i < nBuckets; i++ {
		t := firstT + int64(i)*size
		if metric == "tokens" {
			buckets = append(buckets, map[string]any{
				"t": t, "tok_in": tokIn[i], "tok_cached": tokCached[i],
				"tok_cache_creation": 0, "tok_reasoning": tokReasoning[i], "tok_out": tokOut[i],
			})
		} else {
			// No tps column in the Go schema; emit the shape with zeros so the
			// chart renders an empty series instead of crashing on undefined.
			buckets = append(buckets, map[string]any{
				"t": t, "tps_avg": 0.0, "tps_p95": 0.0, "tps_sample_ratio": 0.0,
			})
		}
	}
	c.JSON(http.StatusOK, gin.H{"bucket_seconds": size, "metric": metric, "buckets": buckets})
}

func (a *App) adminGetAlertRules(c *gin.Context) {
	v, ok, _ := a.State.Store.GetSetting("alert_rules")
	if !ok || strings.TrimSpace(v) == "" {
		v = "[]"
	}
	var rules []any
	if err := json.Unmarshal([]byte(v), &rules); err != nil {
		// The stored value may already be the console envelope {rules:[...]}.
		obj := decodeJSONObject(v)
		if raw, ok := obj["rules"].([]any); ok {
			rules = raw
		} else {
			rules = []any{}
		}
	}
	c.JSON(http.StatusOK, gin.H{"rules": rules})
}

func (a *App) adminPutAlertRules(c *gin.Context) {
	raw, err := c.GetRawData()
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	_ = a.State.Store.SetSetting("alert_rules", string(raw))
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func queryInt(c *gin.Context, key string, def int) int {
	if v := c.Query(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func queryInt64(c *gin.Context, key string, def int64) int64 {
	if v := c.Query(key); v != "" {
		if n, err := strconv.ParseInt(v, 10, 64); err == nil {
			return n
		}
	}
	return def
}

var _ = auth.ErrUnauthorized

// -- provider / key-pool / deployment mutations ----------------------------

func (a *App) findProvider(name string) *config.Provider {
	for i := range a.State.Cfg.Providers {
		if a.State.Cfg.Providers[i].Name == name {
			return &a.State.Cfg.Providers[i]
		}
	}
	return nil
}

// providerDefaultBaseURL mirrors Python's _default_base_url: the catalog's
// default base URL for a provider type ("" when the type has none, e.g.
// openai-compatible, which requires an explicit URL).
func providerDefaultBaseURL(ptype string) string {
	switch ptype {
	case "openai":
		return "https://api.openai.com/v1"
	case "anthropic":
		return "https://api.anthropic.com/v1"
	case "gemini":
		return "https://generativelanguage.googleapis.com/v1beta"
	case "openrouter":
		return "https://openrouter.ai/api/v1"
	case "cline":
		return "https://api.cline.bot/api/v1"
	case "gmicloud":
		return "https://api.gmi-serving.com/v1"
	case "bai":
		return "https://api.b.ai/v1"
	case "workbuddy":
		return "https://copilot.tencent.com"
	case "nvidia-nim":
		return "https://integrate.api.nvidia.com/v1"
	case "opencode":
		return "https://opencode.ai/inference/v1"
	case "hermes":
		return "https://inference-api.nousresearch.com/v1"
	case "ifm":
		return "https://api.ifm.ai/v1"
	}
	return ""
}

func isProviderType(ptype string) bool {
	for _, t := range config.ProviderTypes() {
		if t == ptype {
			return true
		}
	}
	return false
}

func (a *App) adminCreateProvider(c *gin.Context) {
	// The console's Add-provider form posts {name, provider_type, base_url?,
	// label, key} (single key). Older clients used {provider, keys:[...]}; accept
	// both so the form works and nothing else regresses.
	var body struct {
		Name         string            `json:"name"`
		ProviderType string            `json:"provider_type"`
		Provider     string            `json:"provider"`
		BaseURL      string            `json:"base_url"`
		Label        string            `json:"label"`
		Key          string            `json:"key"`
		AliasID      string            `json:"alias_id"`
		TimeoutS     float64           `json:"timeout_s"`
		ExtraHeaders map[string]string `json:"extra_headers"`
		Keys         []config.KeyDef   `json:"keys"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body"})
		return
	}
	name := strings.TrimSpace(body.Name)
	ptype := strings.TrimSpace(body.ProviderType)
	if ptype == "" {
		ptype = strings.TrimSpace(body.Provider)
	}
	if ptype == "" {
		ptype = "openai-compatible"
	}
	baseURL := strings.TrimSpace(body.BaseURL)
	if baseURL == "" {
		baseURL = providerDefaultBaseURL(ptype)
	}
	keys := body.Keys
	if len(keys) == 0 && body.Key != "" {
		label := strings.TrimSpace(body.Label)
		if label == "" {
			label = "default"
		}
		keys = []config.KeyDef{{Label: label, Key: body.Key}}
	}
	if name == "" || len(keys) == 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "name and key are required"})
		return
	}
	if !isProviderType(ptype) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "unsupported provider type '" + ptype + "'"})
		return
	}
	if baseURL == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "base_url is required for provider type '" + ptype + "'"})
		return
	}
	if a.findProvider(name) != nil {
		c.JSON(http.StatusConflict, gin.H{"error": "provider '" + name + "' already exists"})
		return
	}
	for i := range keys {
		if keys[i].Label == "" {
			keys[i].Label = "default"
		}
		if keys[i].Weight < 1 {
			keys[i].Weight = 1
		}
		keys[i].Enabled = true
	}
	p := config.Provider{
		Name: name, ProviderType: ptype, BaseURL: baseURL,
		TimeoutS: body.TimeoutS, ExtraHeaders: body.ExtraHeaders, Keys: keys,
		RoundRobin: true, AliasID: body.AliasID,
	}
	a.State.Cfg.Providers = append(a.State.Cfg.Providers, p)
	_ = saveProvider(a.State.Store, p)
	a.State.Auth.Audit(a.actor(c), "provider.create", name, "")
	a.rebuild()
	var alias any
	if body.AliasID != "" {
		alias = body.AliasID
	}
	c.JSON(http.StatusOK, gin.H{
		"name": name, "provider_type": ptype, "base_url": baseURL, "alias_id": alias,
	})
}

func (a *App) adminPatchProvider(c *gin.Context) {
	p := a.findProvider(c.Param("name"))
	if p == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "provider not found"})
		return
	}
	var body struct {
		BaseURL      *string           `json:"base_url"`
		TimeoutS     *float64          `json:"timeout_s"`
		ExtraHeaders map[string]string `json:"extra_headers"`
	}
	_ = c.ShouldBindJSON(&body)
	if body.BaseURL != nil {
		p.BaseURL = *body.BaseURL
	}
	if body.TimeoutS != nil {
		p.TimeoutS = *body.TimeoutS
	}
	if body.ExtraHeaders != nil {
		p.ExtraHeaders = body.ExtraHeaders
	}
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "provider.patch", p.Name, "")
	a.rebuild()
	c.JSON(http.StatusOK, a.consoleProviderFromConfig(*p))
}

func (a *App) adminDeleteProvider(c *gin.Context) {
	name := c.Param("name")
	found := false
	kept := a.State.Cfg.Providers[:0]
	for _, p := range a.State.Cfg.Providers {
		if p.Name == name {
			found = true
			continue
		}
		kept = append(kept, p)
	}
	if !found {
		c.JSON(http.StatusNotFound, gin.H{"error": "provider not found"})
		return
	}
	a.State.Cfg.Providers = kept
	models := a.State.Cfg.ModelList[:0]
	for _, m := range a.State.Cfg.ModelList {
		if m.Params.Provider == name {
			continue
		}
		models = append(models, m)
	}
	a.State.Cfg.ModelList = models
	_ = deleteProvider(a.State.Store, name)
	a.State.Auth.Audit(a.actor(c), "provider.delete", name, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"deleted": true, "name": name})
}

func (a *App) adminAddProviderKey(c *gin.Context) {
	p := a.findProvider(c.Param("name"))
	if p == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "provider not found"})
		return
	}
	var k config.KeyDef
	if err := c.ShouldBindJSON(&k); err != nil || k.Key == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "key is required"})
		return
	}
	if k.Label == "" {
		k.Label = "default"
	}
	if k.Weight < 1 {
		k.Weight = 1
	}
	k.Enabled = true
	p.Keys = append(p.Keys, k)
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "provider.key.add", p.Name+"/"+k.Label, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"key": consolePoolKey(k)})
}

func (a *App) adminPatchProviderKey(c *gin.Context) {
	p := a.findProvider(c.Param("name"))
	if p == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "provider not found"})
		return
	}
	var body struct {
		Weight  *int  `json:"weight"`
		Enabled *bool `json:"enabled"`
	}
	_ = c.ShouldBindJSON(&body)
	found := false
	for i := range p.Keys {
		if p.Keys[i].Label != c.Param("label") {
			continue
		}
		found = true
		if body.Weight != nil && *body.Weight >= 1 {
			p.Keys[i].Weight = *body.Weight
		}
		if body.Enabled != nil {
			p.Keys[i].Enabled = *body.Enabled
		}
	}
	if !found {
		c.JSON(http.StatusNotFound, gin.H{"error": "key not found"})
		return
	}
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "provider.key.patch", p.Name+"/"+c.Param("label"), "")
	a.rebuild()
	for _, k := range p.Keys {
		if k.Label == c.Param("label") {
			c.JSON(http.StatusOK, gin.H{"key": consolePoolKey(k)})
			return
		}
	}
	c.JSON(http.StatusOK, gin.H{"key": nil})
}

func (a *App) adminDeleteProviderKey(c *gin.Context) {
	p := a.findProvider(c.Param("name"))
	if p == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "provider not found"})
		return
	}
	kept := p.Keys[:0]
	found := false
	for _, k := range p.Keys {
		if k.Label == c.Param("label") {
			found = true
			continue
		}
		kept = append(kept, k)
	}
	if !found {
		c.JSON(http.StatusNotFound, gin.H{"error": "key not found"})
		return
	}
	p.Keys = kept
	_ = saveProvider(a.State.Store, *p)
	a.State.Auth.Audit(a.actor(c), "provider.key.delete", p.Name+"/"+c.Param("label"), "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"deleted": true, "label": c.Param("label")})
}

func (a *App) adminRevealProviderKey(c *gin.Context) {
	p := a.findProvider(c.Param("name"))
	if p == nil {
		c.JSON(http.StatusNotFound, gin.H{"error": "provider not found"})
		return
	}
	for _, k := range p.Keys {
		if k.Label == c.Param("label") {
			a.State.Auth.Audit(a.actor(c), "provider.key.reveal", p.Name+"/"+k.Label, "")
			c.JSON(http.StatusOK, gin.H{"secret": k.Key, "label": k.Label})
			return
		}
	}
	c.JSON(http.StatusNotFound, gin.H{"error": "key not found"})
}

func (a *App) adminAddDeployment(c *gin.Context) {
	group := c.Param("name")
	var body struct {
		Provider string `json:"provider"`
		ModelID  string `json:"model_id"`
		Model    string `json:"model"`
		Weight   int    `json:"weight"`
	}
	if err := c.ShouldBindJSON(&body); err != nil || body.Provider == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "provider and model are required"})
		return
	}
	modelID := body.ModelID
	if modelID == "" {
		modelID = body.Model
	}
	if modelID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "provider and model are required"})
		return
	}
	weight := body.Weight
	if weight < 1 {
		weight = 1
	}
	if a.findProvider(body.Provider) == nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "unknown provider"})
		return
	}
	p := config.DeploymentParams{Provider: body.Provider, Model: modelID, Weight: weight}
	a.State.Cfg.ModelList = append(a.State.Cfg.ModelList, config.ModelEntry{ModelName: group, Params: p})
	id := group + "|" + p.Provider + "|" + p.Model
	_ = saveDeployment(a.State.Store, id, group, p)
	a.State.Auth.Audit(a.actor(c), "deployment.add", id, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"deployment": map[string]any{
		"provider": body.Provider, "model_id": modelID, "weight": weight,
		"available": true, "inflight": 0, "p95_latency_ms": 0.0,
		"cooldown_remaining_s": 0.0,
	}})
}

func (a *App) adminDeleteDeployment(c *gin.Context) {
	group := a.State.Router.ResolveGroup(c.Param("name"))
	provider := c.Query("provider")
	model := c.Query("model_id")
	if provider == "" && model == "" {
		var body struct {
			Provider string `json:"provider"`
			Model    string `json:"model"`
			ModelID  string `json:"model_id"`
		}
		_ = c.ShouldBindJSON(&body)
		provider, model = body.Provider, body.Model
		if model == "" {
			model = body.ModelID
		}
	}
	kept := a.State.Cfg.ModelList[:0]
	found := false
	for _, m := range a.State.Cfg.ModelList {
		if m.ModelName == group && m.Params.Provider == provider && m.Params.Model == model {
			found = true
			continue
		}
		kept = append(kept, m)
	}
	if !found {
		c.JSON(http.StatusNotFound, gin.H{"error": "deployment not found"})
		return
	}
	a.State.Cfg.ModelList = kept
	_ = deleteDeployment(a.State.Store, group+"|"+provider+"|"+model)
	a.State.Auth.Audit(a.actor(c), "deployment.delete", group, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{
		"deleted": true, "group": group, "provider": provider, "model_id": model,
		"group_emptied": len(a.State.Router.Deployments(group)) == 0,
	})
}

func (a *App) adminAddAlias(c *gin.Context) {
	// The console posts the batch shape {set: {name: value|entry}, unset: []};
	// older clients post a single {name, target, force_mapping}. Accept both.
	var body struct {
		Name         string                     `json:"name"`
		Target       string                     `json:"target"`
		ForceMapping *bool                      `json:"force_mapping"`
		Set          map[string]json.RawMessage `json:"set"`
		Unset        []string                   `json:"unset"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body"})
		return
	}
	if a.State.Cfg.Router.ModelGroupAlias == nil {
		a.State.Cfg.Router.ModelGroupAlias = map[string]config.AliasEntry{}
	}
	apply := func(name string, raw json.RawMessage) {
		var target string
		if json.Unmarshal(raw, &target) == nil && target != "" {
			a.State.Cfg.Router.ModelGroupAlias[name] = config.AliasEntry{Target: target, ForceMapping: true}
			return
		}
		var entry struct {
			Target       string `json:"target"`
			ForceMapping *bool  `json:"force_mapping"`
			DisplayName  string `json:"display_name"`
		}
		if json.Unmarshal(raw, &entry) == nil && entry.Target != "" {
			fm := true
			if entry.ForceMapping != nil {
				fm = *entry.ForceMapping
			}
			a.State.Cfg.Router.ModelGroupAlias[name] = config.AliasEntry{
				Target: entry.Target, ForceMapping: fm, DisplayName: entry.DisplayName,
			}
		}
	}
	if body.Set != nil || body.Unset != nil {
		for name, raw := range body.Set {
			apply(name, raw)
		}
		for _, name := range body.Unset {
			delete(a.State.Cfg.Router.ModelGroupAlias, name)
		}
	} else {
		if body.Name == "" || body.Target == "" {
			c.JSON(http.StatusBadRequest, gin.H{"error": "name and target are required"})
			return
		}
		fm := true
		if body.ForceMapping != nil {
			fm = *body.ForceMapping
		}
		a.State.Cfg.Router.ModelGroupAlias[body.Name] = config.AliasEntry{Target: body.Target, ForceMapping: fm}
	}
	if raw, err := json.Marshal(a.State.Cfg.Router.ModelGroupAlias); err == nil {
		_ = a.State.Store.SetSetting("model_group_alias", string(raw))
	}
	a.State.Auth.Audit(a.actor(c), "alias.update", "", "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"aliases": a.consoleAliases()})
}

func (a *App) adminExportProviders(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{"providers": a.State.Cfg.Providers, "model_list": a.State.Cfg.ModelList})
}

func (a *App) adminImportProviders(c *gin.Context) {
	var body struct {
		Providers []config.Provider   `json:"providers"`
		ModelList []config.ModelEntry `json:"model_list"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	for _, p := range body.Providers {
		a.State.Cfg.Providers = append(a.State.Cfg.Providers, p)
		_ = saveProvider(a.State.Store, p)
	}
	for _, m := range body.ModelList {
		a.State.Cfg.ModelList = append(a.State.Cfg.ModelList, m)
		_ = saveDeployment(a.State.Store, m.ModelName+"|"+m.Params.Provider+"|"+m.Params.Model, m.ModelName, m.Params)
	}
	a.State.Auth.Audit(a.actor(c), "providers.import", "", "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) exportTable(q string, cols []string) []map[string]any {
	rows, err := a.State.Store.Query(a.State.Store.Rebind(q))
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		vals := make([]any, len(cols))
		ptrs := make([]any, len(cols))
		for i := range vals {
			ptrs[i] = &vals[i]
		}
		if rows.Scan(ptrs...) != nil {
			continue
		}
		m := make(map[string]any, len(cols))
		for i, c := range cols {
			if b, ok := vals[i].([]byte); ok {
				m[c] = string(b)
			} else {
				m[c] = vals[i]
			}
		}
		out = append(out, m)
	}
	return out
}

func mapStr(m map[string]any, key string) string {
	if v, ok := m[key].(string); ok {
		return v
	}
	return ""
}

func mapVal(m map[string]any, key string) any {
	if v, ok := m[key]; ok {
		return v
	}
	return nil
}
