package server

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"wiwi-go/internal/config"
	"wiwi-go/internal/cost"
)

// Whole-gateway backup/restore in the shape wiwi/server/backup.py defines, so
// the console's Import/Export page works against either backend.

var backupSectionOrder = []string{
	"providers", "keys", "deployments", "aliases", "settings", "prices",
	"users", "vkeys", "logs", "rollups", "audit", "responses",
}

var backupBulkSections = map[string]bool{
	"logs": true, "rollups": true, "audit": true, "responses": true,
}

func parseBackupSections(raw string) []string {
	if strings.TrimSpace(raw) == "" {
		out := []string{}
		for _, s := range backupSectionOrder {
			if !backupBulkSections[s] {
				out = append(out, s)
			}
		}
		return out
	}
	wanted := map[string]bool{}
	for _, s := range strings.Split(raw, ",") {
		wanted[strings.TrimSpace(s)] = true
	}
	out := []string{}
	for _, s := range backupSectionOrder {
		if wanted[s] {
			out = append(out, s)
		}
	}
	return out
}

func queryBool(c *gin.Context, key string) bool {
	v := strings.ToLower(c.Query(key))
	return v == "1" || v == "true" || v == "yes"
}

// buildBackupDoc assembles the export document. Sections not requested are
// absent (not empty), matching the Python contract.
func (a *App) buildBackupDoc(sections []string, includeSecrets, includeBodies bool) map[string]any {
	doc := map[string]any{
		"kind": "wiwi.backup", "version": 1,
		"exported_at":     float64(time.Now().UnixNano()) / 1e9,
		"sections":        sections,
		"include_secrets": includeSecrets, "include_bodies": includeBodies,
	}
	counts := map[string]any{}
	for _, section := range sections {
		switch section {
		case "providers":
			rows := []map[string]any{}
			ps := append([]config.Provider(nil), a.State.Cfg.Providers...)
			sort.Slice(ps, func(i, j int) bool { return ps[i].Name < ps[j].Name })
			for _, p := range ps {
				headers := p.ExtraHeaders
				if headers == nil {
					headers = map[string]string{}
				}
				rows = append(rows, map[string]any{
					"name": p.Name, "provider_type": p.ProviderType, "base_url": p.BaseURL,
					"timeout_s": p.TimeoutS, "extra_headers": headers,
					"round_robin": p.RoundRobin, "alias_id": nilIfEmpty(p.AliasID),
				})
			}
			doc[section] = rows
			counts[section] = len(rows)
		case "keys":
			rows := []map[string]any{}
			for _, p := range a.State.Cfg.Providers {
				for _, k := range p.Keys {
					row := map[string]any{
						"provider_name": p.Name, "label": k.Label,
						"weight": k.Weight, "enabled": k.Enabled,
					}
					if includeSecrets {
						row["secret"] = k.Key
					}
					rows = append(rows, row)
				}
			}
			doc[section] = rows
			counts[section] = len(rows)
		case "deployments":
			rows := []map[string]any{}
			for _, m := range a.State.Cfg.ModelList {
				row := map[string]any{
					"group_name": m.ModelName, "provider_name": m.Params.Provider,
					"model_id": m.Params.Model, "weight": m.Params.Weight,
					"prompt_cache": m.Params.PromptCache,
				}
				if m.Params.RPM != nil {
					row["rpm"] = *m.Params.RPM
				}
				if m.Params.TPM != nil {
					row["tpm"] = *m.Params.TPM
				}
				if m.Params.Timeout != nil {
					row["timeout"] = *m.Params.Timeout
				}
				if m.Params.MaxTokens != nil {
					row["max_tokens"] = *m.Params.MaxTokens
				}
				rows = append(rows, row)
			}
			doc[section] = rows
			counts[section] = len(rows)
		case "aliases":
			doc[section] = a.consoleAliases()
			counts[section] = map[string]any{}
		case "settings":
			out := map[string]any{}
			if settings, err := a.State.Store.ListSettings(); err == nil {
				for k, v := range settings {
					if k == "model_group_alias" {
						continue
					}
					out[k] = decodeJSONValue(v)
				}
			}
			doc[section] = out
			counts[section] = map[string]any{}
		case "prices":
			rows := []map[string]any{}
			raw := a.exportTable(
				`SELECT model_id,input_cost_per_token,output_cost_per_token,cache_read_input_cost_per_token FROM model_prices ORDER BY model_id`,
				[]string{"model_id", "input_cost_per_token", "output_cost_per_token", "cache_read_input_cost_per_token"})
			for _, r := range raw {
				row := map[string]any{
					"model_id":              mapStr(r, "model_id"),
					"input_cost_per_token":  numOr(r["input_cost_per_token"], 0),
					"output_cost_per_token": numOr(r["output_cost_per_token"], 0),
				}
				if cached := numOr(r["cache_read_input_cost_per_token"], 0); cached != 0 {
					row["cache_read_input_cost_per_token"] = cached
				}
				rows = append(rows, row)
			}
			doc[section] = rows
			counts[section] = len(rows)
		case "users":
			rows := a.exportTable(`SELECT id,username,password_hash,role,created_at,COALESCE(disabled,0) FROM users ORDER BY username`,
				[]string{"id", "username", "password_hash", "role", "created_at", "disabled"})
			out := make([]map[string]any, 0, len(rows))
			for _, r := range rows {
				out = append(out, map[string]any{
					"id": r["id"], "username": r["username"], "password_hash": r["password_hash"],
					"role": r["role"], "created_at": r["created_at"],
					"disabled": numOr(r["disabled"], 0) != 0,
				})
			}
			doc[section] = out
			counts[section] = len(out)
		case "vkeys":
			rows := a.exportTable(`SELECT id,key_hash,key_alias,models,max_budget,spend_to_date,budget_reserved,rpm,tpm,expires_at,disabled,owner_id,created_at FROM vkeys ORDER BY created_at`,
				[]string{"id", "key_hash", "key_alias", "models", "max_budget", "spend_to_date", "budget_reserved", "rpm", "tpm", "expires_at", "disabled", "owner_id", "created_at"})
			out := make([]map[string]any, 0, len(rows))
			for _, r := range rows {
				var models []string
				if s, ok := r["models"].(string); ok && s != "" {
					_ = json.Unmarshal([]byte(s), &models)
				}
				if models == nil {
					models = []string{}
				}
				out = append(out, map[string]any{
					"id": r["id"], "key_hash": r["key_hash"], "key_alias": r["key_alias"],
					"models": models, "max_budget": r["max_budget"],
					"spend_to_date": r["spend_to_date"], "budget_reserved": r["budget_reserved"],
					"rpm": r["rpm"], "tpm": r["tpm"], "expires_at": r["expires_at"],
					"disabled": numOr(r["disabled"], 0) != 0, "owner_id": nilIfEmpty(mapStr(r, "owner_id")),
					"created_at": r["created_at"],
				})
			}
			doc[section] = out
			counts[section] = len(out)
		case "logs":
			cols := []string{"id", "request_id", "ts", "key_id", "model_group", "provider", "model_id", "status", "tok_in", "tok_out", "tok_cached", "tok_reasoning", "cost", "latency_ms", "ttft_ms", "error_message"}
			rows := a.exportTable(`SELECT `+strings.Join(cols, ",")+` FROM request_logs ORDER BY ts DESC LIMIT 10000`, cols)
			doc[section] = map[string]any{"request_logs": rows}
			counts[section] = map[string]any{"request_logs": len(rows)}
		case "audit":
			rows := a.exportTable(`SELECT id,ts,actor,action,target,diff FROM audit_logs ORDER BY ts DESC`,
				[]string{"id", "ts", "actor", "action", "target", "diff"})
			for _, r := range rows {
				if s, ok := r["diff"].(string); ok {
					r["diff"] = decodeJSONObject(s)
				}
			}
			doc[section] = map[string]any{"audit_logs": rows}
			counts[section] = map[string]any{"audit_logs": len(rows)}
		case "rollups":
			doc[section] = map[string]any{"request_rollups": []any{}}
			counts[section] = map[string]any{"request_rollups": 0}
		case "responses":
			doc[section] = map[string]any{"stored_responses": []any{}}
			counts[section] = map[string]any{"stored_responses": 0}
		}
	}
	doc["counts"] = counts
	return doc
}

func nilIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

// decodeJSONValue decodes a stored setting, falling back to the raw string for
// rows written before settings were JSON-encoded (e.g. "routing_strategy").
func decodeJSONValue(raw string) any {
	var v any
	if json.Unmarshal([]byte(raw), &v) == nil {
		return v
	}
	return raw
}

func (a *App) adminBackup(c *gin.Context) {
	sections := parseBackupSections(c.Query("sections"))
	doc := a.buildBackupDoc(sections, queryBool(c, "include_secrets"), queryBool(c, "include_bodies"))
	c.JSON(http.StatusOK, doc)
}

// adminBackupImport merges a document. With ?dry_run=1 it returns the per-
// section counts it would apply and writes nothing.
func (a *App) adminBackupImport(c *gin.Context) {
	raw, err := c.GetRawData()
	if err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid JSON"})
		return
	}
	if kind, ok := doc["kind"].(string); ok && kind != "" && kind != "wiwi.backup" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "not a wiwi backup document"})
		return
	}
	sections := parseBackupSections(c.Query("sections"))
	if q := c.Query("sections"); strings.TrimSpace(q) == "" {
		// Fall back to the document's own section list.
		if list, ok := doc["sections"].([]any); ok && len(list) > 0 {
			parts := make([]string, 0, len(list))
			for _, s := range list {
				if str, ok := s.(string); ok {
					parts = append(parts, str)
				}
			}
			sections = parseBackupSections(strings.Join(parts, ","))
		}
	}
	dryRun := queryBool(c, "dry_run")
	counts := map[string]any{}
	for _, section := range sections {
		value, present := doc[section]
		if !present {
			continue
		}
		n := 0
		if !dryRun {
			n = a.applyBackupSection(section, value)
		} else {
			n = backupSectionCount(value)
		}
		counts[section] = n
	}
	if dryRun {
		c.JSON(http.StatusOK, gin.H{"dry_run": true, "sections": sections, "counts": counts})
		return
	}
	// Also accept the legacy Go shape ({config, model_list, prices: map}).
	a.applyLegacyBackup(doc)
	c.JSON(http.StatusOK, gin.H{
		"providers": countOf(counts, "providers"), "keys": countOf(counts, "keys"),
		"deployments": countOf(counts, "deployments"), "aliases": countOf(counts, "aliases"),
		"settings": countOf(counts, "settings"), "prices": countOf(counts, "prices"),
		"users": countOf(counts, "users"), "vkeys": countOf(counts, "vkeys"),
		"logs":          0,
		"rollups":       map[string]any{"request_rollups": 0},
		"audit_skipped": 0, "settings_skipped": 0,
		"responses": map[string]any{"stored_responses": 0},
	})
}

func countOf(counts map[string]any, key string) int {
	if n, ok := counts[key].(int); ok {
		return n
	}
	return 0
}

func backupSectionCount(value any) int {
	switch v := value.(type) {
	case []any:
		return len(v)
	case map[string]any:
		n := 0
		for _, inner := range v {
			if list, ok := inner.([]any); ok {
				n += len(list)
			}
		}
		return n
	}
	return 0
}

// applyBackupSection merges one section and returns the number of rows applied.
func (a *App) applyBackupSection(section string, value any) int {
	switch section {
	case "providers":
		list, _ := value.([]any)
		for _, item := range list {
			m, _ := item.(map[string]any)
			name := mapStr(m, "name")
			if name == "" {
				continue
			}
			p := config.Provider{
				Name: name, ProviderType: mapStr(m, "provider_type"),
				BaseURL: mapStr(m, "base_url"), RoundRobin: mapBool(m, "round_robin", true),
				AliasID: mapStr(m, "alias_id"),
			}
			if f, ok := m["timeout_s"].(float64); ok {
				p.TimeoutS = f
			}
			if hdr, ok := m["extra_headers"].(map[string]any); ok {
				p.ExtraHeaders = map[string]string{}
				for k, v := range hdr {
					if s, ok := v.(string); ok {
						p.ExtraHeaders[k] = s
					}
				}
			}
			if existing := a.findProvider(name); existing != nil {
				existing.ProviderType = p.ProviderType
				existing.BaseURL = p.BaseURL
				existing.RoundRobin = p.RoundRobin
				existing.AliasID = p.AliasID
				_ = saveProvider(a.State.Store, *existing)
			} else {
				a.State.Cfg.Providers = append(a.State.Cfg.Providers, p)
				_ = saveProvider(a.State.Store, p)
			}
		}
		a.rebuild()
		return len(list)
	case "keys":
		list, _ := value.([]any)
		n := 0
		for _, item := range list {
			m, _ := item.(map[string]any)
			p := a.findProvider(mapStr(m, "provider_name"))
			if p == nil {
				continue
			}
			label := mapStr(m, "label")
			if label == "" {
				continue
			}
			k := config.KeyDef{
				Label: label, Key: mapStr(m, "secret"),
				Weight: int(numOr(m["weight"], 1)), Enabled: mapBool(m, "enabled", true),
			}
			replaced := false
			for i := range p.Keys {
				if p.Keys[i].Label == label {
					if k.Key == "" {
						k.Key = p.Keys[i].Key
					}
					p.Keys[i] = k
					replaced = true
					break
				}
			}
			if !replaced {
				p.Keys = append(p.Keys, k)
			}
			_ = saveProvider(a.State.Store, *p)
			n++
		}
		a.rebuild()
		return n
	case "deployments":
		list, _ := value.([]any)
		n := 0
		for _, item := range list {
			m, _ := item.(map[string]any)
			group, provider, model := mapStr(m, "group_name"), mapStr(m, "provider_name"), mapStr(m, "model_id")
			if group == "" || provider == "" || model == "" {
				continue
			}
			params := config.DeploymentParams{
				Provider: provider, Model: model, Weight: int(numOr(m["weight"], 1)),
				PromptCache: mapBool(m, "prompt_cache", false),
			}
			a.State.Cfg.ModelList = append(a.State.Cfg.ModelList, config.ModelEntry{ModelName: group, Params: params})
			_ = saveDeployment(a.State.Store, group, provider, model, params)
			n++
		}
		a.rebuild()
		return n
	case "aliases":
		m, _ := value.(map[string]any)
		if a.State.Cfg.Router.ModelGroupAlias == nil {
			a.State.Cfg.Router.ModelGroupAlias = map[string]config.AliasEntry{}
		}
		n := 0
		for name, v := range m {
			switch entry := v.(type) {
			case string:
				a.State.Cfg.Router.ModelGroupAlias[name] = config.AliasEntry{Target: entry, ForceMapping: true}
				n++
			case map[string]any:
				a.State.Cfg.Router.ModelGroupAlias[name] = config.AliasEntry{
					Target: mapStr(entry, "target"), ForceMapping: mapBool(entry, "force_mapping", true),
					DisplayName: mapStr(entry, "display_name"),
				}
				n++
			case nil:
				delete(a.State.Cfg.Router.ModelGroupAlias, name)
				n++
			}
		}
		a.rebuild()
		return n
	case "settings":
		m, _ := value.(map[string]any)
		n := 0
		for k, v := range m {
			// A string setting is stored raw (that is how the gateway's own
			// setters write routing_strategy); everything else is JSON-encoded.
			if s, ok := v.(string); ok {
				_ = a.State.Store.SetSetting(k, s)
			} else {
				b, _ := json.Marshal(v)
				_ = a.State.Store.SetSetting(k, string(b))
			}
			n++
		}
		return n
	case "prices":
		n := 0
		switch rows := value.(type) {
		case []any:
			for _, item := range rows {
				m, _ := item.(map[string]any)
				model := mapStr(m, "model_id")
				if model == "" {
					continue
				}
				p := cost.ModelPrice{
					Prompt:     numOr(m["input_cost_per_token"], 0),
					Completion: numOr(m["output_cost_per_token"], 0),
					Cached:     numOr(m["cache_read_input_cost_per_token"], 0),
				}
				a.upsertPrice(model, p)
				n++
			}
		case map[string]any:
			// Legacy Go shape: {model: {Prompt, Completion, Cached}}.
			for model, item := range rows {
				m, _ := item.(map[string]any)
				a.upsertPrice(model, cost.ModelPrice{
					Prompt:     numOr(m["Prompt"], numOr(m["prompt"], 0)),
					Completion: numOr(m["Completion"], numOr(m["completion"], 0)),
					Cached:     numOr(m["Cached"], numOr(m["cached"], 0)),
				})
				n++
			}
		}
		return n
	case "users":
		list, _ := value.([]any)
		n := 0
		for _, item := range list {
			m, _ := item.(map[string]any)
			username := mapStr(m, "username")
			if username == "" {
				continue
			}
			id := mapStr(m, "id")
			if id == "" {
				id = "usr_" + username
			}
			disabled := 0
			if mapBool(m, "disabled", false) {
				disabled = 1
			}
			now := numOr(m["created_at"], float64(time.Now().Unix()))
			_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
				`INSERT INTO users(id,username,password_hash,role,disabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?)
				 ON CONFLICT(id) DO UPDATE SET username=excluded.username,password_hash=excluded.password_hash,role=excluded.role,disabled=excluded.disabled,updated_at=excluded.updated_at`),
				id, username, mapStr(m, "password_hash"), mapStr(m, "role"), disabled, now, now)
			n++
		}
		return n
	case "vkeys":
		list, _ := value.([]any)
		n := 0
		for _, item := range list {
			m, _ := item.(map[string]any)
			keyHash := mapStr(m, "key_hash")
			if keyHash == "" {
				continue
			}
			id := mapStr(m, "id")
			if id == "" {
				id = "key_" + keyHash[:8]
			}
			models, _ := json.Marshal(m["models"])
			disabled := 0
			if mapBool(m, "disabled", false) {
				disabled = 1
			}
			now := numOr(m["created_at"], float64(time.Now().Unix()))
			var expires any
			if e := m["expires_at"]; e != nil {
				expires = e
			}
			var owner any
			if o := mapStr(m, "owner_id"); o != "" {
				owner = o
			}
			_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
				`INSERT INTO vkeys(id,key_hash,key_alias,models,max_budget,spend_to_date,rpm,tpm,expires_at,disabled,owner_id,created_at,updated_at,budget_reserved)
				 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
				 ON CONFLICT(id) DO UPDATE SET key_hash=excluded.key_hash,key_alias=excluded.key_alias,max_budget=excluded.max_budget,
				 spend_to_date=excluded.spend_to_date,rpm=excluded.rpm,tpm=excluded.tpm,models=excluded.models,disabled=excluded.disabled,
				 owner_id=excluded.owner_id,expires_at=excluded.expires_at,budget_reserved=excluded.budget_reserved,updated_at=excluded.updated_at`),
				id, keyHash, mapStr(m, "key_alias"), string(models),
				m["max_budget"], numOr(m["spend_to_date"], 0),
				m["rpm"], m["tpm"], expires, disabled, owner, now, now,
				numOr(m["budget_reserved"], 0))
			n++
		}
		return n
	}
	return 0
}

func (a *App) upsertPrice(model string, p cost.ModelPrice) {
	a.State.Cost.Upsert(model, p)
	_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
		`INSERT INTO model_prices(model_id,input_cost_per_token,output_cost_per_token,cache_read_input_cost_per_token) VALUES(?,?,?,?)
		 ON CONFLICT(model_id) DO UPDATE SET input_cost_per_token=excluded.input_cost_per_token,
		 output_cost_per_token=excluded.output_cost_per_token,
		 cache_read_input_cost_per_token=excluded.cache_read_input_cost_per_token`),
		model, p.Prompt, p.Completion, p.Cached)
}

// applyLegacyBackup accepts the pre-console Go document shape.
func (a *App) applyLegacyBackup(doc map[string]any) {
	if cfgRaw, ok := doc["config"]; ok {
		if b, err := json.Marshal(cfgRaw); err == nil {
			var cfg config.Config
			if json.Unmarshal(b, &cfg) == nil {
				for _, p := range cfg.Providers {
					if a.findProvider(p.Name) == nil {
						a.State.Cfg.Providers = append(a.State.Cfg.Providers, p)
						_ = saveProvider(a.State.Store, p)
					}
				}
				for _, m := range cfg.ModelList {
					a.State.Cfg.ModelList = append(a.State.Cfg.ModelList, m)
					_ = saveDeployment(a.State.Store, m.ModelName, m.Params.Provider, m.Params.Model, m.Params)
				}
			}
		}
	}
	if list, ok := doc["model_list"].([]any); ok {
		for _, item := range list {
			if b, err := json.Marshal(item); err == nil {
				var m config.ModelEntry
				if json.Unmarshal(b, &m) == nil && m.ModelName != "" {
					a.State.Cfg.ModelList = append(a.State.Cfg.ModelList, m)
					_ = saveDeployment(a.State.Store, m.ModelName, m.Params.Provider, m.Params.Model, m.Params)
				}
			}
		}
	}
	a.rebuild()
}

func mapBool(m map[string]any, key string, def bool) bool {
	if v, ok := m[key].(bool); ok {
		return v
	}
	return def
}
