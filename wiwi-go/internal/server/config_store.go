package server

import (
	"encoding/json"
	"log"

	"wiwi-go/internal/config"
	"wiwi-go/internal/core"
	"wiwi-go/internal/cost"
	"wiwi-go/internal/router"
	"wiwi-go/internal/store"
)

// DB-backed config overlay: providers, deployments, prices and settings
// created or mutated through /admin survive a restart. On boot the overlay is
// merged on top of the YAML config before the router is built.
//
// The tables are Python's (wiwi/server/config_store.py): a provider's fields
// live in real columns with its keys in provider_keys rows, a deployment is
// keyed by the (group_name, provider_name, model_id) triple, and removal is a
// `detached=1` tombstone, not a DELETE.

// loadRuntimeSettings applies the console-owned settings that affect routing
// (strategy and model-group aliases) on top of the YAML config.
func loadRuntimeSettings(cfg *config.Config, st *store.Store) {
	if v, ok, _ := st.GetSetting("routing_strategy"); ok && v != "" {
		cfg.Router.Strategy = v
	}
	raw, ok, _ := st.GetSetting("model_group_alias")
	if !ok || raw == "" {
		return
	}
	var aliases map[string]config.AliasEntry
	if json.Unmarshal([]byte(raw), &aliases) != nil {
		return
	}
	if cfg.Router.ModelGroupAlias == nil {
		cfg.Router.ModelGroupAlias = map[string]config.AliasEntry{}
	}
	for name, entry := range aliases {
		cfg.Router.ModelGroupAlias[name] = entry
	}
}

// loadOverlay merges DB rows into the config. A row with detached=1 is a
// tombstone: the account (YAML- or DB-sourced) is dropped instead of layered.
func loadOverlay(cfg *config.Config, st *store.Store) {
	tombstoned := loadProviderRows(cfg, st)
	loadProviderKeys(cfg, st)
	applyProviderTombstones(cfg, tombstoned)
	loadDeploymentRows(cfg, st)
	cfg.ApplyDefaults()
}

// loadProviderRows reads the providers table, layering column overrides onto
// same-named YAML accounts and appending unknown ones. Returns the set of
// tombstoned names.
func loadProviderRows(cfg *config.Config, st *store.Store) map[string]bool {
	rows, err := st.Query(st.Rebind(
		`SELECT name,provider_type,base_url,timeout_s,extra_headers,round_robin,enabled,alias_id,detached
		 FROM providers`))
	if err != nil {
		return nil
	}
	defer rows.Close()
	byName := map[string]*config.Provider{}
	for i := range cfg.Providers {
		byName[cfg.Providers[i].Name] = &cfg.Providers[i]
	}
	tombstones := map[string]bool{}
	for rows.Next() {
		var name, ptype, baseURL, hdrsRaw, aliasID string
		var timeoutS float64
		var roundRobin, enabled, detached int
		if err := rows.Scan(&name, &ptype, &baseURL, &timeoutS, &hdrsRaw,
			&roundRobin, &enabled, &aliasID, &detached); err != nil {
			continue
		}
		if detached == 1 {
			tombstones[name] = true
			continue
		}
		var hdrs map[string]string
		_ = json.Unmarshal([]byte(hdrsRaw), &hdrs)
		if existing, ok := byName[name]; ok {
			existing.ProviderType = ptype
			existing.BaseURL = baseURL
			if timeoutS > 0 {
				existing.TimeoutS = timeoutS
			}
			if hdrs != nil {
				existing.ExtraHeaders = hdrs
			}
			existing.AliasID = aliasID
			continue
		}
		cfg.Providers = append(cfg.Providers, config.Provider{
			Name: name, ProviderType: ptype, BaseURL: baseURL,
			TimeoutS: timeoutS, ExtraHeaders: hdrs, AliasID: aliasID,
			RoundRobin: roundRobin != 0,
		})
		_ = enabled // Go's Provider has no enabled flag; detached is the lever
	}
	return tombstones
}

// loadProviderKeys attaches provider_keys rows (detached=0) to their provider.
func loadProviderKeys(cfg *config.Config, st *store.Store) {
	rows, err := st.Query(st.Rebind(
		`SELECT provider_name,label,secret,weight FROM provider_keys WHERE detached=0`))
	if err != nil {
		return
	}
	defer rows.Close()
	for rows.Next() {
		var pname, label, secret string
		var weight int
		if err := rows.Scan(&pname, &label, &secret, &weight); err != nil {
			continue
		}
		if weight <= 0 {
			weight = 1
		}
		for i := range cfg.Providers {
			if cfg.Providers[i].Name == pname {
				cfg.Providers[i].Keys = append(cfg.Providers[i].Keys,
					config.KeyDef{Label: label, Key: secret, Weight: weight})
			}
		}
	}
}

// applyProviderTombstones drops tombstoned accounts, YAML copies included.
func applyProviderTombstones(cfg *config.Config, tombstones map[string]bool) {
	if len(tombstones) == 0 {
		return
	}
	kept := make([]config.Provider, 0, len(cfg.Providers))
	for _, p := range cfg.Providers {
		if tombstones[p.Name] {
			continue
		}
		kept = append(kept, p)
	}
	cfg.Providers = kept
}

// loadDeploymentRows reads the deployments table, honoring tombstones by
// dropping the matching YAML-sourced triple instead of layering the row.
func loadDeploymentRows(cfg *config.Config, st *store.Store) {
	rows, err := st.Query(st.Rebind(
		`SELECT group_name,provider_name,model_id,weight,detached,rpm,tpm,timeout,max_tokens,prompt_cache
		 FROM deployments`))
	if err != nil {
		return
	}
	defer rows.Close()
	for rows.Next() {
		var group, provider, model string
		var weight, detached int
		var rpm, tpm, maxTokens, promptCache *int
		var timeout *float64
		if err := rows.Scan(&group, &provider, &model, &weight, &detached,
			&rpm, &tpm, &timeout, &maxTokens, &promptCache); err != nil {
			continue
		}
		if detached == 1 {
			// Tombstone: drop a YAML-sourced deployment of this triple.
			kept := cfg.ModelList[:0]
			for _, e := range cfg.ModelList {
				if e.ModelName == group && e.Params.Provider == provider &&
					e.Params.Model == model {
					continue
				}
				kept = append(kept, e)
			}
			cfg.ModelList = kept
			continue
		}
		if weight <= 0 {
			weight = 1
		}
		cfg.ModelList = append(cfg.ModelList, config.ModelEntry{
			ModelName: group,
			Params: config.DeploymentParams{
				Provider: provider, Model: model, Weight: weight,
				RPM: rpm, TPM: tpm, Timeout: timeout,
				MaxTokens: maxTokens, PromptCache: intToBool(promptCache),
			},
		})
	}
}

func intToBool(p *int) bool {
	return p != nil && *p != 0
}

// loadPrices seeds the cost engine from model_prices (Python's columns).
func loadPrices(st *store.Store, engine *cost.Engine) {
	rows, err := st.Query(st.Rebind(
		`SELECT model_id,input_cost_per_token,output_cost_per_token,cache_read_input_cost_per_token
		 FROM model_prices`))
	if err != nil {
		return
	}
	defer rows.Close()
	prices := map[string]cost.ModelPrice{}
	for rows.Next() {
		var model string
		var p cost.ModelPrice
		var cached *float64
		if err := rows.Scan(&model, &p.Prompt, &p.Completion, &cached); err != nil {
			continue
		}
		if cached != nil {
			p.Cached = *cached
		}
		prices[model] = p
	}
	if len(prices) > 0 {
		engine.Load(prices)
	}
}

// saveProvider persists a provider (upsert with re-attach) and its keys.
func saveProvider(st *store.Store, p config.Provider) error {
	hdrs := "{}"
	if p.ExtraHeaders != nil {
		if raw, err := json.Marshal(p.ExtraHeaders); err == nil {
			hdrs = string(raw)
		}
	}
	rr := 0
	if p.RoundRobin {
		rr = 1
	}
	// Go's Provider carries no enabled flag: the column is written 1 and
	// detached is the removal lever (Python's add_provider default).
	timeout := p.TimeoutS
	if timeout <= 0 {
		timeout = 120.0
	}
	if _, err := st.DB().Exec(st.Rebind(
		`INSERT INTO providers(name,provider_type,base_url,timeout_s,extra_headers,round_robin,enabled,alias_id,detached)
		 VALUES(?,?,?,?,?,?,1,?,0)
		 ON CONFLICT(name) DO UPDATE SET provider_type=excluded.provider_type,base_url=excluded.base_url,
		 timeout_s=excluded.timeout_s,extra_headers=excluded.extra_headers,round_robin=excluded.round_robin,
		 enabled=1,alias_id=excluded.alias_id,detached=0`),
		p.Name, p.ProviderType, p.BaseURL, timeout, hdrs, rr, p.AliasID); err != nil {
		return err
	}
	for _, k := range p.Keys {
		w := k.Weight
		if w <= 0 {
			w = 1
		}
		if _, err := st.DB().Exec(st.Rebind(
			`INSERT INTO provider_keys(provider_name,label,secret,weight,enabled,detached)
			 VALUES(?,?,?,?,1,0)
			 ON CONFLICT(provider_name,label) DO UPDATE SET secret=excluded.secret,weight=excluded.weight,enabled=1,detached=0`),
			p.Name, k.Label, k.Key, w); err != nil {
			return err
		}
	}
	return nil
}

func deleteProvider(st *store.Store, name string) error {
	if _, err := st.DB().Exec(st.Rebind(`DELETE FROM provider_keys WHERE provider_name=?`), name); err != nil {
		// provider_keys may not exist in this schema; ignore
		_ = err
	}
	if _, err := st.DB().Exec(st.Rebind(`DELETE FROM deployments WHERE provider_name=?`), name); err != nil {
		_ = err
	}
	_, err := st.DB().Exec(st.Rebind(`DELETE FROM providers WHERE name=?`), name)
	return err
}

func saveDeployment(st *store.Store, group, provider, model string, p config.DeploymentParams) error {
	weight := p.Weight
	if weight <= 0 {
		weight = 1
	}
	_, err := st.DB().Exec(st.Rebind(
		`INSERT INTO deployments(group_name,provider_name,model_id,weight,detached,rpm,tpm,timeout,max_tokens,prompt_cache)
		 VALUES(?,?,?,?,0,?,?,?,?,?)
		 ON CONFLICT(group_name,provider_name,model_id) DO UPDATE SET weight=excluded.weight,detached=0,
		 rpm=excluded.rpm,tpm=excluded.tpm,timeout=excluded.timeout,max_tokens=excluded.max_tokens,prompt_cache=excluded.prompt_cache`),
		group, provider, model, weight, p.RPM, p.TPM, p.Timeout, p.MaxTokens, boolIntCol(p.PromptCache))
	return err
}

func boolIntCol(b bool) int {
	if b {
		return 1
	}
	return 0
}

func deleteDeployment(st *store.Store, group, provider, model string) error {
	// Tombstone first so a YAML-sourced triple stays gone after restart;
	// then hard-delete so list endpoints drop the row now too.
	if _, err := st.DB().Exec(st.Rebind(
		`UPDATE deployments SET detached=1 WHERE group_name=? AND provider_name=? AND model_id=?`),
		group, provider, model); err != nil {
		return err
	}
	_, err := st.DB().Exec(st.Rebind(
		`DELETE FROM deployments WHERE group_name=? AND provider_name=? AND model_id=?`),
		group, provider, model)
	return err
}

// rebuild reconstructs the router/gateway after a config mutation. In-flight
// requests keep the old router; new requests see the change.
func (a *App) rebuild() {
	a.State.Router = router.New(a.State.Cfg)
	a.State.Gateway = core.New(a.State.Router, a.State.Cost)
	a.State.Gateway.DropParams = a.State.Cfg.Wiwi.DropParams
	log.Printf("wiwi: config reloaded (%d providers, %d deployments)", len(a.State.Cfg.Providers), len(a.State.Cfg.ModelList))
}
