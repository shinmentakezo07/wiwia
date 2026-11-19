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

// DB-backed config overlay: providers, deployments, prices and settings created
// or mutated through /admin survive a restart. On boot the overlay is merged on
// top of the YAML config before the router is built.

type dbProvider struct {
	ProviderType string            `json:"provider_type"`
	BaseURL      string            `json:"base_url"`
	ExtraHeaders map[string]string `json:"extra_headers"`
	TimeoutS     float64           `json:"timeout_s"`
	Keys         []config.KeyDef   `json:"keys"`
	AliasID      string            `json:"alias_id"`
}

type dbDeployment struct {
	Provider  string         `json:"provider"`
	Model     string         `json:"model"`
	Weight    int            `json:"weight"`
	MaxTokens *int           `json:"max_tokens,omitempty"`
	ExtraBody map[string]any `json:"extra_body,omitempty"`
}

// loadOverlay merges DB-managed providers/deployments into cfg.
// loadRuntimeSettings applies the console-owned settings that affect routing
// (strategy and model-group aliases) on top of the YAML config, so a change
// made through /admin survives a restart.
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

func loadOverlay(cfg *config.Config, st *store.Store) {
	rows, err := st.Query(st.Rebind(`SELECT name,provider_type,base_url,config FROM providers`))
	if err != nil {
		return
	}
	defer rows.Close()
	byName := map[string]*config.Provider{}
	for i := range cfg.Providers {
		byName[cfg.Providers[i].Name] = &cfg.Providers[i]
	}
	for rows.Next() {
		var name, ptype, baseURL, raw string
		if rows.Scan(&name, &ptype, &baseURL, &raw) != nil {
			continue
		}
		var d dbProvider
		_ = json.Unmarshal([]byte(raw), &d)
		if d.ProviderType == "" {
			d.ProviderType = ptype
		}
		if d.BaseURL == "" {
			d.BaseURL = baseURL
		}
		if existing, ok := byName[name]; ok {
			existing.ProviderType = d.ProviderType
			existing.BaseURL = d.BaseURL
			if d.TimeoutS > 0 {
				existing.TimeoutS = d.TimeoutS
			}
			if d.ExtraHeaders != nil {
				existing.ExtraHeaders = d.ExtraHeaders
			}
			if len(d.Keys) > 0 {
				existing.Keys = d.Keys
			}
			continue
		}
		p := config.Provider{
			Name: name, ProviderType: d.ProviderType, BaseURL: d.BaseURL,
			TimeoutS: d.TimeoutS, ExtraHeaders: d.ExtraHeaders, Keys: d.Keys,
			AliasID: d.AliasID, RoundRobin: true,
		}
		cfg.Providers = append(cfg.Providers, p)
	}

	drows, err := st.Query(st.Rebind(`SELECT id,model_name,provider,model,weight,config FROM deployments`))
	if err != nil {
		return
	}
	defer drows.Close()
	for drows.Next() {
		var id, group, provider, model, raw string
		var weight int
		if drows.Scan(&id, &group, &provider, &model, &weight, &raw) != nil {
			continue
		}
		var d dbDeployment
		_ = json.Unmarshal([]byte(raw), &d)
		cfg.ModelList = append(cfg.ModelList, config.ModelEntry{
			ModelName: group,
			Params: config.DeploymentParams{
				Provider: provider, Model: model, Weight: weight,
				MaxTokens: d.MaxTokens, ExtraBody: d.ExtraBody,
			},
		})
	}
	cfg.ApplyDefaults()
}

// loadPrices seeds the cost engine from model_prices.
func loadPrices(st *store.Store, engine *cost.Engine) {
	rows, err := st.Query(st.Rebind(`SELECT model,prompt,completion,cached FROM model_prices`))
	if err != nil {
		return
	}
	defer rows.Close()
	prices := map[string]cost.ModelPrice{}
	for rows.Next() {
		var model string
		var p cost.ModelPrice
		if rows.Scan(&model, &p.Prompt, &p.Completion, &p.Cached) != nil {
			continue
		}
		prices[model] = p
	}
	if len(prices) > 0 {
		engine.Load(prices)
	}
}

// saveProvider persists a provider (upsert).
func saveProvider(st *store.Store, p config.Provider) error {
	d := dbProvider{
		ProviderType: p.ProviderType, BaseURL: p.BaseURL, ExtraHeaders: p.ExtraHeaders,
		TimeoutS: p.TimeoutS, Keys: p.Keys, AliasID: p.AliasID,
	}
	raw, _ := json.Marshal(d)
	_, err := st.DB().Exec(st.Rebind(
		`INSERT INTO providers(name,provider_type,base_url,config) VALUES(?,?,?,?)
		 ON CONFLICT(name) DO UPDATE SET provider_type=excluded.provider_type,base_url=excluded.base_url,config=excluded.config`),
		p.Name, p.ProviderType, p.BaseURL, string(raw))
	return err
}

func deleteProvider(st *store.Store, name string) error {
	if _, err := st.DB().Exec(st.Rebind(`DELETE FROM provider_keys WHERE provider=?`), name); err != nil {
		// provider_keys may not exist in this schema; ignore
		_ = err
	}
	_, err := st.DB().Exec(st.Rebind(`DELETE FROM providers WHERE name=?`), name)
	return err
}

func saveDeployment(st *store.Store, id, group string, p config.DeploymentParams) error {
	d := dbDeployment{Provider: p.Provider, Model: p.Model, Weight: p.Weight, MaxTokens: p.MaxTokens, ExtraBody: p.ExtraBody}
	raw, _ := json.Marshal(d)
	_, err := st.DB().Exec(st.Rebind(
		`INSERT INTO deployments(id,model_name,provider,model,weight,config) VALUES(?,?,?,?,?,?)
		 ON CONFLICT(id) DO UPDATE SET model_name=excluded.model_name,provider=excluded.provider,model=excluded.model,weight=excluded.weight,config=excluded.config`),
		id, group, p.Provider, p.Model, p.Weight, string(raw))
	return err
}

func deleteDeployment(st *store.Store, id string) error {
	_, err := st.DB().Exec(st.Rebind(`DELETE FROM deployments WHERE id=?`), id)
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
