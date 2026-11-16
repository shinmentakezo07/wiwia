package server

import (
	"encoding/json"
	"net/http"
	"strconv"
	"time"

	"github.com/gin-gonic/gin"

	"wiwi-go/internal/auth"
	"wiwi-go/internal/config"
	"wiwi-go/internal/cost"
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
	g.POST("/model-groups/:name/deployments/delete", a.adminDeleteDeployment)
	g.GET("/providers/export", a.adminExportProviders)
	g.POST("/providers/import", a.adminImportProviders)

	// pricing
	g.GET("/pricing", a.adminListPricing)
	g.PUT("/pricing/:id", a.adminUpsertPricing)
	g.DELETE("/pricing/:id", a.adminDeletePricing)

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
	}
	_ = c.ShouldBindJSON(&body)
	owner := ""
	if ai := authOf(c); ai != nil && !ai.IsMaster {
		owner = ai.UserID
	}
	plain, v, err := a.State.Auth.GenerateOwned(owner, body.Name, body.MaxBudget, body.RPM, body.TPM, body.Models, body.TTL, a.State.Cfg.General.MaxKeysPerUser)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "key.generate", v.ID, "")
	c.JSON(http.StatusOK, gin.H{"key": plain, "id": v.ID, "name": v.Name})
}

func (a *App) adminListKeys(c *gin.Context) {
	keys, err := a.State.Auth.List()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"data": keys})
}

func (a *App) adminPatchKey(c *gin.Context) {
	var body struct {
		Name      *string  `json:"name"`
		MaxBudget *float64 `json:"max_budget"`
		RPM       *int     `json:"rpm"`
		TPM       *int     `json:"tpm"`
		Models    []string `json:"models"`
	}
	_ = c.ShouldBindJSON(&body)
	if err := a.State.Auth.Patch(c.Param("id"), body.MaxBudget, body.RPM, body.TPM, body.Models, body.Name); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "key.patch", c.Param("id"), "")
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminDeleteKey(c *gin.Context) {
	if err := a.State.Auth.Delete(c.Param("id")); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "key.delete", c.Param("id"), "")
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminDisableKey(c *gin.Context) {
	if err := a.State.Auth.SetEnabled(c.Param("id"), false); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "key.disable", c.Param("id"), "")
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminListUsers(c *gin.Context) {
	users, err := a.State.Auth.ListUsers()
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"data": users})
}

func (a *App) adminPatchUser(c *gin.Context) {
	var body struct {
		Role string `json:"role"`
	}
	_ = c.ShouldBindJSON(&body)
	if err := a.State.Auth.SetUserRole(c.Param("uid"), body.Role); err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	a.State.Auth.Audit(a.actor(c), "user.patch", c.Param("uid"), body.Role)
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminProviderCatalog(c *gin.Context) {
	types := config.ProviderTypes()
	c.JSON(http.StatusOK, gin.H{"data": types})
}

func (a *App) adminListProviders(c *gin.Context) {
	out := []map[string]any{}
	for name, p := range a.State.Router.Providers() {
		out = append(out, map[string]any{
			"name": name, "provider": p.Type, "base_url": p.BaseURL, "keys": len(p.Keys),
		})
	}
	c.JSON(http.StatusOK, gin.H{"data": out})
}

func (a *App) adminModels(c *gin.Context) {
	out := []map[string]any{}
	for _, g := range a.State.Router.Groups() {
		deps := a.State.Router.Deployments(g)
		ds := []map[string]any{}
		for _, d := range deps {
			ds = append(ds, map[string]any{
				"provider": d.Provider.Name, "model": d.Model, "weight": d.Weight,
			})
		}
		out = append(out, map[string]any{"model_name": g, "deployments": ds})
	}
	c.JSON(http.StatusOK, gin.H{"data": out})
}

func (a *App) adminPatchModelGroup(c *gin.Context) {
	// Runtime group mutation is not persisted to YAML; acknowledge and audit.
	a.State.Auth.Audit(a.actor(c), "model_group.patch", c.Param("name"), "")
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminListAliases(c *gin.Context) {
	out := []map[string]any{}
	for name, al := range a.State.Cfg.Router.ModelGroupAlias {
		out = append(out, map[string]any{"name": name, "target": al.Target, "force_mapping": al.ForceMapping})
	}
	c.JSON(http.StatusOK, gin.H{"data": out})
}

func (a *App) adminListPricing(c *gin.Context) {
	all := a.State.Cost.All()
	out := []map[string]any{}
	for model, p := range all {
		out = append(out, map[string]any{
			"model": model, "input": p.Prompt, "output": p.Completion, "cached": p.Cached,
		})
	}
	c.JSON(http.StatusOK, gin.H{"data": out})
}

func (a *App) adminUpsertPricing(c *gin.Context) {
	var body struct {
		Input  float64 `json:"input"`
		Output float64 `json:"output"`
		Cached float64 `json:"cached"`
	}
	_ = c.ShouldBindJSON(&body)
	model := c.Param("id")
	a.State.Cost.Upsert(model, cost.ModelPrice{Prompt: body.Input, Completion: body.Output, Cached: body.Cached})
	_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
		`INSERT INTO model_prices(model,prompt,completion,cached) VALUES(?,?,?,?)
		 ON CONFLICT(model) DO UPDATE SET prompt=excluded.prompt,completion=excluded.completion,cached=excluded.cached`),
		model, body.Input, body.Output, body.Cached)
	a.State.Auth.Audit(a.actor(c), "pricing.upsert", model, "")
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminDeletePricing(c *gin.Context) {
	model := c.Param("id")
	a.State.Cost.Delete(model)
	_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(`DELETE FROM model_prices WHERE model=?`), model)
	a.State.Auth.Audit(a.actor(c), "pricing.delete", model, "")
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminLogsRequests(c *gin.Context) {
	limit := queryInt(c, "limit", 100)
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT id,request_id,ts,key_id,model_group,provider,model_id,status,prompt_tokens,
		 completion_tokens,cached_tokens,reasoning_tokens,cost,latency_ms,ttft_ms,error
		 FROM request_logs ORDER BY ts DESC LIMIT ?`), limit)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var id, rid, keyID, group, prov, model, errText string
		var ts int64
		var status, pt, ct, cached, reasoning, lat, ttft int
		var costv float64
		if err := rows.Scan(&id, &rid, &ts, &keyID, &group, &prov, &model, &status,
			&pt, &ct, &cached, &reasoning, &costv, &lat, &ttft, &errText); err != nil {
			continue
		}
		out = append(out, map[string]any{
			"id": id, "request_id": rid, "ts": ts, "key_id": keyID, "model_group": group,
			"provider": prov, "model_id": model, "status": status, "prompt_tokens": pt,
			"completion_tokens": ct, "cached_tokens": cached, "reasoning_tokens": reasoning,
			"cost": costv, "latency_ms": lat, "ttft_ms": ttft, "error": errText,
		})
	}
	c.JSON(http.StatusOK, gin.H{"data": out})
}

func (a *App) adminLogsAudit(c *gin.Context) {
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT id,ts,actor,action,target,diff FROM audit_logs ORDER BY ts DESC LIMIT ?`), queryInt(c, "limit", 100))
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
		out = append(out, map[string]any{"id": id, "ts": ts, "actor": actor, "action": action, "target": target, "diff": diff})
	}
	c.JSON(http.StatusOK, gin.H{"data": out})
}

func (a *App) adminLogsProxy(c *gin.Context) {
	events := a.State.Bus.EventsSince(queryInt64(c, "since", 0))
	out := []map[string]any{}
	for _, e := range events {
		out = append(out, map[string]any{"id": e.ID, "ts": e.Ts, "type": e.Type, "data": e.Data})
	}
	c.JSON(http.StatusOK, gin.H{"data": out})
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
	flusher, _ := c.Writer.(http.Flusher)
	timeout := time.After(30 * time.Minute)
	for {
		select {
		case ev, ok := <-ch:
			if !ok {
				return
			}
			b, _ := json.Marshal(ev)
			c.Writer.Write([]byte("id: " + strconv.FormatInt(ev.ID, 10) + "\n"))
			c.Writer.Write([]byte("event: " + ev.Type + "\n"))
			c.Writer.Write([]byte("data: " + string(b) + "\n\n"))
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
	snap := a.State.Totals.Snapshot()
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT COUNT(*), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), COALESCE(SUM(cost),0) FROM request_logs`))
	var count, pt, ct int64
	var totalCost float64
	if err == nil {
		defer rows.Close()
		if rows.Next() {
			_ = rows.Scan(&count, &pt, &ct, &totalCost)
		}
	}
	var rRows, rPrompt, rCompletion int64
	var rCost float64
	if rr, err := a.State.Store.Query(`SELECT COALESCE(SUM(requests),0), COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0), COALESCE(SUM(cost),0) FROM request_rollups`); err == nil {
		defer rr.Close()
		if rr.Next() {
			_ = rr.Scan(&rRows, &rPrompt, &rCompletion, &rCost)
		}
	}
	c.JSON(http.StatusOK, gin.H{
		"requests": snap["requests"], "errors": snap["errors"],
		"prompt_tokens": snap["prompt_tokens"], "completion_tokens": snap["completion_tokens"],
		"cost": snap["cost"], "db_rows": count + rRows, "db_prompt_tokens": pt + rPrompt,
		"db_completion_tokens": ct + rCompletion, "db_cost": totalCost + rCost,
		"rollup_rows": rRows,
	})
}

func (a *App) adminStatsTimeseries(c *gin.Context) {
	bucket := queryInt64(c, "bucket", 3600)
	if bucket <= 0 {
		bucket = 3600
	}
	metric := c.Query("metric")
	col := "COUNT(*)"
	switch metric {
	case "prompt_tokens":
		col = "COALESCE(SUM(prompt_tokens),0)"
	case "completion_tokens":
		col = "COALESCE(SUM(completion_tokens),0)"
	case "cost":
		col = "COALESCE(SUM(cost),0)"
	}
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT (ts/?) AS bucket, `+col+` AS value
		 FROM request_logs GROUP BY bucket ORDER BY bucket DESC LIMIT ?`), bucket, queryInt(c, "limit", 168))
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	defer rows.Close()
	out := []map[string]any{}
	for rows.Next() {
		var b int64
		var value float64
		if rows.Scan(&b, &value) != nil {
			continue
		}
		out = append(out, map[string]any{"bucket": b, "value": value})
	}
	c.JSON(http.StatusOK, gin.H{"data": out})
}

func (a *App) adminBackup(c *gin.Context) {
	keys := a.exportTable(`SELECT id,name,owner,key_hash,max_budget,spend,rpm,tpm,models,enabled,expires_at,created_at,budget_reserved FROM vkeys`,
		[]string{"id", "name", "owner", "key_hash", "max_budget", "spend", "rpm", "tpm", "models", "enabled", "expires_at", "created_at", "budget_reserved"})
	users := a.exportTable(`SELECT id,username,pw_hash,role,created_at FROM users`,
		[]string{"id", "username", "pw_hash", "role", "created_at"})
	settings, _ := a.State.Store.ListSettings()
	cfgRaw, _ := json.Marshal(a.State.Cfg)
	c.JSON(http.StatusOK, gin.H{
		"version": 1, "config": json.RawMessage(cfgRaw), "keys": keys, "users": users,
		"prices": a.State.Cost.All(), "providers": a.State.Cfg.Providers,
		"model_list": a.State.Cfg.ModelList, "settings": settings,
	})
}

func (a *App) adminBackupImport(c *gin.Context) {
	var body struct {
		Prices    map[string]cost.ModelPrice `json:"prices"`
		Providers []config.Provider          `json:"providers"`
		ModelList []config.ModelEntry        `json:"model_list"`
		Settings  map[string]string          `json:"settings"`
		Keys      []map[string]any           `json:"keys"`
		Users     []map[string]any           `json:"users"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	if body.Prices != nil {
		a.State.Cost.Load(body.Prices)
		for model, p := range body.Prices {
			_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
				`INSERT INTO model_prices(model,prompt,completion,cached) VALUES(?,?,?,?)
				 ON CONFLICT(model) DO UPDATE SET prompt=excluded.prompt,completion=excluded.completion,cached=excluded.cached`),
				model, p.Prompt, p.Completion, p.Cached)
		}
	}
	if body.Settings != nil {
		for k, v := range body.Settings {
			_ = a.State.Store.SetSetting(k, v)
		}
	}
	for _, p := range body.Providers {
		if a.findProvider(p.Name) == nil {
			a.State.Cfg.Providers = append(a.State.Cfg.Providers, p)
		}
		_ = saveProvider(a.State.Store, p)
	}
	for _, m := range body.ModelList {
		a.State.Cfg.ModelList = append(a.State.Cfg.ModelList, m)
		_ = saveDeployment(a.State.Store, m.ModelName+"|"+m.Params.Provider+"|"+m.Params.Model, m.ModelName, m.Params)
	}
	for _, k := range body.Keys {
		id := mapStr(k, "id")
		if id == "" {
			continue
		}
		_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
			`INSERT INTO vkeys(id,name,owner,key_hash,max_budget,spend,rpm,tpm,models,enabled,expires_at,created_at,budget_reserved)
			 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
			 ON CONFLICT(id) DO UPDATE SET name=excluded.name,owner=excluded.owner,key_hash=excluded.key_hash,
			 max_budget=excluded.max_budget,spend=excluded.spend,rpm=excluded.rpm,tpm=excluded.tpm,
			 models=excluded.models,enabled=excluded.enabled,expires_at=excluded.expires_at,
			 created_at=excluded.created_at,budget_reserved=excluded.budget_reserved`),
			id, mapStr(k, "name"), mapStr(k, "owner"), mapStr(k, "key_hash"),
			mapVal(k, "max_budget"), mapVal(k, "spend"), mapVal(k, "rpm"), mapVal(k, "tpm"),
			mapStr(k, "models"), mapVal(k, "enabled"), mapVal(k, "expires_at"),
			mapVal(k, "created_at"), mapVal(k, "budget_reserved"))
	}
	for _, u := range body.Users {
		id := mapStr(u, "id")
		if id == "" {
			continue
		}
		_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
			`INSERT INTO users(id,username,pw_hash,role,created_at) VALUES(?,?,?,?,?)
			 ON CONFLICT(id) DO UPDATE SET username=excluded.username,pw_hash=excluded.pw_hash,
			 role=excluded.role,created_at=excluded.created_at`),
			id, mapStr(u, "username"), mapStr(u, "pw_hash"), mapStr(u, "role"), mapVal(u, "created_at"))
	}
	a.State.Auth.Audit(a.actor(c), "backup.import", "backup", "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminGetAlertRules(c *gin.Context) {
	v, ok, _ := a.State.Store.GetSetting("alert_rules")
	if !ok {
		v = "[]"
	}
	c.Data(http.StatusOK, "application/json", []byte(v))
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

func (a *App) adminCreateProvider(c *gin.Context) {
	var body struct {
		Name         string            `json:"name"`
		Provider     string            `json:"provider"`
		BaseURL      string            `json:"base_url"`
		TimeoutS     float64           `json:"timeout_s"`
		ExtraHeaders map[string]string `json:"extra_headers"`
		Keys         []config.KeyDef   `json:"keys"`
	}
	if err := c.ShouldBindJSON(&body); err != nil || body.Name == "" || body.Provider == "" || len(body.Keys) == 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "name, provider and at least one key are required"})
		return
	}
	if a.findProvider(body.Name) != nil {
		c.JSON(http.StatusConflict, gin.H{"error": "provider already exists"})
		return
	}
	for i := range body.Keys {
		if body.Keys[i].Label == "" {
			body.Keys[i].Label = "default"
		}
		if body.Keys[i].Weight < 1 {
			body.Keys[i].Weight = 1
		}
		body.Keys[i].Enabled = true
	}
	p := config.Provider{
		Name: body.Name, ProviderType: body.Provider, BaseURL: body.BaseURL,
		TimeoutS: body.TimeoutS, ExtraHeaders: body.ExtraHeaders, Keys: body.Keys,
		RoundRobin: true,
	}
	a.State.Cfg.Providers = append(a.State.Cfg.Providers, p)
	_ = saveProvider(a.State.Store, p)
	a.State.Auth.Audit(a.actor(c), "provider.create", body.Name, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"ok": true})
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
	c.JSON(http.StatusOK, gin.H{"ok": true})
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
	c.JSON(http.StatusOK, gin.H{"ok": true})
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
	c.JSON(http.StatusOK, gin.H{"ok": true})
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
	c.JSON(http.StatusOK, gin.H{"ok": true})
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
	c.JSON(http.StatusOK, gin.H{"ok": true})
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
			c.JSON(http.StatusOK, gin.H{"key": k.Key, "label": k.Label})
			return
		}
	}
	c.JSON(http.StatusNotFound, gin.H{"error": "key not found"})
}

func (a *App) adminAddDeployment(c *gin.Context) {
	group := c.Param("name")
	var p config.DeploymentParams
	if err := c.ShouldBindJSON(&p); err != nil || p.Provider == "" || p.Model == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "provider and model are required"})
		return
	}
	if p.Weight < 1 {
		p.Weight = 1
	}
	if a.findProvider(p.Provider) == nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "unknown provider"})
		return
	}
	a.State.Cfg.ModelList = append(a.State.Cfg.ModelList, config.ModelEntry{ModelName: group, Params: p})
	id := group + "|" + p.Provider + "|" + p.Model
	_ = saveDeployment(a.State.Store, id, group, p)
	a.State.Auth.Audit(a.actor(c), "deployment.add", id, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"ok": true, "id": id})
}

func (a *App) adminDeleteDeployment(c *gin.Context) {
	group := c.Param("name")
	var body struct {
		Provider string `json:"provider"`
		Model    string `json:"model"`
	}
	_ = c.ShouldBindJSON(&body)
	kept := a.State.Cfg.ModelList[:0]
	found := false
	for _, m := range a.State.Cfg.ModelList {
		if m.ModelName == group && m.Params.Provider == body.Provider && m.Params.Model == body.Model {
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
	_ = deleteDeployment(a.State.Store, group+"|"+body.Provider+"|"+body.Model)
	a.State.Auth.Audit(a.actor(c), "deployment.delete", group, "")
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) adminAddAlias(c *gin.Context) {
	var body struct {
		Name         string `json:"name"`
		Target       string `json:"target"`
		ForceMapping *bool  `json:"force_mapping"`
	}
	if err := c.ShouldBindJSON(&body); err != nil || body.Name == "" || body.Target == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "name and target are required"})
		return
	}
	fm := true
	if body.ForceMapping != nil {
		fm = *body.ForceMapping
	}
	if a.State.Cfg.Router.ModelGroupAlias == nil {
		a.State.Cfg.Router.ModelGroupAlias = map[string]config.AliasEntry{}
	}
	a.State.Cfg.Router.ModelGroupAlias[body.Name] = config.AliasEntry{Target: body.Target, ForceMapping: fm}
	a.State.Auth.Audit(a.actor(c), "alias.add", body.Name, body.Target)
	a.rebuild()
	c.JSON(http.StatusOK, gin.H{"ok": true})
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
