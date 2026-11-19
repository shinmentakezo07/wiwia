package server

import (
	"encoding/json"
	"fmt"
	"sort"
	"time"

	"wiwi-go/internal/auth"
	"wiwi-go/internal/config"
	"wiwi-go/internal/logging"
)

// This file holds the response shapes the console (web/) consumes. The Python
// backend is the reference implementation; these helpers keep the Go port
// byte-compatible on the wire so the same SPA runs against either backend.

// builtinProvider is one card in the Add-provider catalog. Ported from
// wiwi/router/router.py:BUILTIN_PROVIDER_TYPES (the single source of truth in
// Python). The web console reads provider_type/label/default_base_url and
// renders the rest as help text.
type builtinProvider struct {
	ProviderType   string   `json:"provider_type"`
	Label          string   `json:"label"`
	DefaultBaseURL string   `json:"default_base_url"`
	Description    string   `json:"description"`
	LatestModels   []string `json:"latest_models"`
	ContextWindow  string   `json:"context_window"`
	DocsURL        string   `json:"docs_url"`
}

var builtinProviders = []builtinProvider{
	{
		ProviderType: "openai", Label: "OpenAI",
		DefaultBaseURL: "https://api.openai.com/v1",
		Description: "GPT-5.6 family models via the OpenAI Chat Completions and " +
			"Responses APIs. Supports reasoning effort, tool use, structured " +
			"outputs, and vision.",
		LatestModels:  []string{"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"},
		ContextWindow: "1.05M tokens",
		DocsURL:       "https://developers.openai.com/api/docs/models",
	},
	{
		ProviderType: "anthropic", Label: "Anthropic",
		DefaultBaseURL: "https://api.anthropic.com/v1",
		Description: "Claude models via the Anthropic Messages API. Features adaptive " +
			"thinking, tool use, vision, and 1M-token context windows on Opus " +
			"and Sonnet.",
		LatestModels:  []string{"claude-fable-5", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"},
		ContextWindow: "1M tokens",
		DocsURL:       "https://platform.claude.com/docs/en/about-claude/models/overview",
	},
	{
		ProviderType: "gemini", Label: "Google Gemini",
		DefaultBaseURL: "https://generativelanguage.googleapis.com/v1beta",
		Description: "Gemini models via the Google Generative Language API. " +
			"Multimodal input (text, images, video, audio), structured output, " +
			"function calling, and the Interactions API.",
		LatestModels:  []string{"gemini-3.1-pro-preview", "gemini-3.7-flash", "gemini-3.5-flash-lite"},
		ContextWindow: "1M+ tokens",
		DocsURL:       "https://ai.google.dev/gemini-api/docs",
	},
	{
		ProviderType: "openrouter", Label: "OpenRouter",
		DefaultBaseURL: "https://openrouter.ai/api/v1",
		Description: "Unified gateway to 400+ models from 60+ providers via a single " +
			"OpenAI-compatible endpoint. Automatic fallbacks, reasoning parameter " +
			"translation, and a latest-alias system.",
		LatestModels:  []string{"~openai/gpt-latest", "~anthropic/claude-sonnet-latest", "google/gemini-3.1-pro-preview", "minimax/minimax-m3"},
		ContextWindow: "varies per model",
		DocsURL:       "https://openrouter.ai/docs/quickstart",
	},
	{
		ProviderType: "openai-compatible", Label: "OpenAI-compatible",
		DefaultBaseURL: "",
		Description: "Any endpoint that speaks the OpenAI Chat Completions wire format " +
			"(e.g. vLLM, Ollama, Together, Groq, DeepSeek). Provide a custom base URL.",
		LatestModels:  []string{},
		ContextWindow: "varies by endpoint",
		DocsURL:       "",
	},
	{
		ProviderType: "cline", Label: "Cline",
		DefaultBaseURL: "https://api.cline.bot/api/v1",
		Description: "Cline's provider gateway (api.cline.bot) via an OpenAI-compatible " +
			"Chat Completions API with streaming-only responses. Authenticates with a " +
			"Cline account OAuth token (WorkOS) and requires Cline client-identification " +
			"headers, which wiwi sends automatically.",
		LatestModels:  []string{"z-ai/glm-5.2", "claude-sonnet-5", "gpt-5.5"},
		ContextWindow: "varies per model",
		DocsURL:       "https://cline.bot",
	},
	{
		ProviderType: "gmicloud", Label: "GMI Cloud",
		DefaultBaseURL: "https://api.gmi-serving.com/v1",
		Description: "Serverless and dedicated GPU inference for 70+ open-source and " +
			"frontier models (DeepSeek, GLM, Llama, Qwen, Claude, GPT, Gemini, Kimi) " +
			"via an OpenAI-compatible Chat Completions API.",
		LatestModels:  []string{"deepseek-ai/DeepSeek-V3.2", "zai-org/GLM-5-FP8", "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "Qwen/Qwen3.6-Plus", "openai/gpt-5.5", "google/gemini-3.1-pro-preview", "anthropic/claude-opus-4.7", "moonshotai/Kimi-K2.6"},
		ContextWindow: "varies per model",
		DocsURL:       "https://docs.gmicloud.ai/quickstart",
	},
	{
		ProviderType: "bai", Label: "B.AI",
		DefaultBaseURL: "https://api.b.ai/v1",
		Description: "B.AI's unified LLM service (api.b.ai): one API key across the " +
			"OpenAI Chat Completions, OpenAI Responses, and Anthropic Messages " +
			"protocols. Hosts DeepSeek thinking-mode models.",
		LatestModels:  []string{"deepseek-v4-flash-vision-exp", "deepseek-v4-flash"},
		ContextWindow: "varies per model",
		DocsURL:       "https://docs.b.ai/llmservice/api/",
	},
	{
		ProviderType: "workbuddy", Label: "WorkBuddy",
		DefaultBaseURL: "https://copilot.tencent.com",
		Description: "WorkBuddy / CodeBuddy (Tencent copilot.tencent.com, workbuddy.ai) " +
			"via an OpenAI-compatible Chat Completions API. Authenticates with an OAuth " +
			"access token + account uid headers and handles token refresh automatically.",
		LatestModels:  []string{"deepseek-v4-flash", "glm-5.3", "kimi-k3", "hy3"},
		ContextWindow: "varies per model",
		DocsURL:       "https://www.codebuddy.cn",
	},
	{
		ProviderType: "nvidia-nim", Label: "NVIDIA NIM",
		DefaultBaseURL: "https://integrate.api.nvidia.com/v1",
		Description: "NVIDIA NIM hosts 50+ open and frontier models (Nemotron, " +
			"DeepSeek, GLM, Llama, Qwen, Kimi, MiniMax, StepFun) via an " +
			"OpenAI-compatible Chat Completions API.",
		LatestModels:  []string{"nvidia/nemotron-3-super-120b-a12b", "nvidia/nemotron-3.5-lightning-30b-a3b", "deepseek-ai/deepseek-v4-pro", "zai-org/glm-5.2", "moonshotai/kimi-k2.6", "minimaxai/minimax-m3", "stepfun-ai/step-3.7-flash"},
		ContextWindow: "varies per model",
		DocsURL:       "https://docs.nvidia.com/nim/large-language-models/latest/",
	},
	{
		ProviderType: "opencode", Label: "OpenCode Zen",
		DefaultBaseURL: "https://opencode.ai/inference/v1",
		Description: "OpenCode Zen gateway (opencode.ai/zen) — curated coding models over " +
			"four upstream protocols routed per model. Authenticates with a Zen API key " +
			"and sends a live User-Agent: opencode/<version>.",
		LatestModels:  []string{"gpt-5.5", "claude-sonnet-5", "gemini-3.1-pro", "deepseek-v4-pro", "glm-5.2", "kimi-k2.6"},
		ContextWindow: "varies per model",
		DocsURL:       "https://opencode.ai/docs/zen",
	},
	{
		ProviderType: "hermes", Label: "Hermes (Nous Portal)",
		DefaultBaseURL: "https://inference-api.nousresearch.com/v1",
		Description: "Nous Portal inference API (OpenAI-compatible). Authenticate with a " +
			"Nous API key or an OAuth token from Hermes Agent.",
		LatestModels:  []string{"Hermes-4-405B", "Hermes-4-70B", "Hermes-4.3-36B"},
		ContextWindow: "varies per model",
		DocsURL:       "https://hermes-agent.nousresearch.com/docs/",
	},
	{
		ProviderType: "ifm", Label: "IFM (MBZUAI)",
		DefaultBaseURL: "https://api.ifm.ai/v1",
		Description: "MBZUAI's Institute of Foundation Models hosted API: K2 Horizon and " +
			"Jais 3 reasoning models (512K context) over an OpenAI-compatible Chat " +
			"Completions endpoint.",
		LatestModels:  []string{"IFM/K2-Horizon-375B-A23B", "IFM/Jais-3-MoVA-36B-A4B"},
		ContextWindow: "512K tokens",
		DocsURL:       "https://docs.ifm.ai/",
	},
}

// providerCatalog renders the catalog with the live `configured` flag, the
// shape /admin/provider-catalog returns (a bare array).
func (a *App) providerCatalog() []map[string]any {
	configured := map[string]bool{}
	for _, p := range a.State.Router.Providers() {
		configured[p.Type] = true
	}
	out := make([]map[string]any, 0, len(builtinProviders))
	for _, p := range builtinProviders {
		out = append(out, map[string]any{
			"provider_type":    p.ProviderType,
			"label":            p.Label,
			"default_base_url": p.DefaultBaseURL,
			"description":      p.Description,
			"latest_models":    p.LatestModels,
			"context_window":   p.ContextWindow,
			"docs_url":         p.DocsURL,
			"builtin":          true,
			"configured":       configured[p.ProviderType],
		})
	}
	return out
}

// consoleKey renders a virtual key in the console's VirtualKey shape.
func consoleKey(k *auth.VKey) map[string]any {
	models := k.Models
	if models == nil {
		models = []string{}
	}
	var maxBudget, rpm, tpm, expires any
	if k.MaxBudget != nil {
		maxBudget = *k.MaxBudget
	}
	if k.RPM != nil {
		rpm = *k.RPM
	}
	if k.TPM != nil {
		tpm = *k.TPM
	}
	if k.ExpiresAt > 0 {
		expires = k.ExpiresAt
	}
	return map[string]any{
		"id": k.ID, "alias": k.Name, "models": models,
		"max_budget": maxBudget, "spend_to_date": k.Spend,
		"rpm": rpm, "tpm": tpm, "expires_at": expires,
		"disabled": !k.Enabled, "budget_reserved": k.BudgetReserved,
	}
}

// consoleUser renders a console account with the fields the users page reads.
func consoleUser(u *auth.User) map[string]any {
	return map[string]any{
		"id": u.ID, "username": u.Username, "role": u.Role,
		"created_at": u.CreatedAt, "disabled": u.Disabled,
	}
}

// consoleModelGroups renders /admin/models' groups array.
func (a *App) consoleModelGroups() []map[string]any {
	now := time.Now()
	names := a.State.Router.Groups()
	sort.Strings(names)
	out := make([]map[string]any, 0, len(names))
	for _, g := range names {
		deps := a.State.Router.Deployments(g)
		ds := make([]map[string]any, 0, len(deps))
		for _, d := range deps {
			ds = append(ds, map[string]any{
				"provider":             d.Provider.Name,
				"model_id":             d.Model,
				"weight":               d.Weight,
				"available":            d.Available(now),
				"inflight":             d.Inflight(),
				"p95_latency_ms":       d.P95LatencyMs(),
				"cooldown_remaining_s": d.CooldownRemaining(now),
			})
		}
		out = append(out, map[string]any{"name": g, "deployments": ds})
	}
	return out
}

// consoleAliases renders the model-group alias map (rich entries become
// {target, force_mapping[, display_name]}, the shape the console's
// ModelAliasEntry declares).
func (a *App) consoleAliases() map[string]any {
	out := map[string]any{}
	for name, al := range a.State.Cfg.Router.ModelGroupAlias {
		var displayName any
		if al.DisplayName != "" {
			displayName = al.DisplayName
		}
		out[name] = map[string]any{
			"target": al.Target, "force_mapping": al.ForceMapping,
			"display_name": displayName, "fork": false,
		}
	}
	return out
}

// consoleProviderAliases is the alias_id -> provider-name map.
func (a *App) consoleProviderAliases() map[string]string {
	out := map[string]string{}
	for name, p := range a.State.Router.Providers() {
		if p.AliasID != "" {
			out[p.AliasID] = name
		}
	}
	return out
}

// consolePublicModels renders /public/models: {groups, aliases}.
func (a *App) consolePublicModels() map[string]any {
	groups := []map[string]any{}
	for _, g := range a.State.Router.Groups() {
		deps := a.State.Router.Deployments(g)
		ds := []map[string]any{}
		for _, d := range deps {
			ds = append(ds, map[string]any{
				"provider": d.Provider.Name, "type": d.Provider.Type, "model_id": d.Model,
			})
		}
		groups = append(groups, map[string]any{"name": g, "deployments": ds})
	}
	aliases := a.consoleAliases()
	return map[string]any{"groups": groups, "aliases": aliases}
}

// consolePrice renders one price row in per-1M units (the engine stores
// per-token rates; the admin API speaks USD per 1M tokens).
func consolePrice(model string, prompt, completion, cached float64) map[string]any {
	return map[string]any{
		"model_id":              model,
		"input_per_1m":          prompt * 1e6,
		"output_per_1m":         completion * 1e6,
		"cache_read_per_1m":     cached * 1e6,
		"cache_creation_per_1m": 0.0,
	}
}

// consolePoolKey renders one provider key in the console's PoolKey shape.
func consolePoolKey(k config.KeyDef) map[string]any {
	status := "active"
	if !k.Enabled {
		status = "disabled"
	}
	masked := ""
	if k.Key != "" {
		masked = "••••"
	}
	return map[string]any{
		"label": k.Label, "masked": masked, "has_secret": k.Key != "",
		"weight": k.Weight, "enabled": k.Enabled, "status": status,
		"cooldown_remaining_s": 0.0, "req_count": 0, "err_count": 0,
		"last_used_ts": nil,
	}
}

// consoleProvider renders one provider account in the console's Provider shape.
func consoleProvider(p config.Provider, healthy bool, keys []map[string]any) map[string]any {
	var aliasID any
	if p.AliasID != "" {
		aliasID = p.AliasID
	}
	return map[string]any{
		"name": p.Name, "provider_type": p.ProviderType, "base_url": p.BaseURL,
		"round_robin": p.RoundRobin, "alias_id": aliasID, "healthy": healthy,
		"keys": keys,
	}
}

// consoleProviderFromConfig renders a provider straight from config (used by
// mutations, where the router has just been rebuilt).
func (a *App) consoleProviderFromConfig(p config.Provider) map[string]any {
	keys := make([]map[string]any, 0, len(p.Keys))
	healthy := len(p.Keys) == 0
	for _, k := range p.Keys {
		keys = append(keys, consolePoolKey(k))
		if k.Enabled {
			healthy = true
		}
	}
	return consoleProvider(p, healthy, keys)
}

// decodeJSONObject decodes a stored JSON string into a map, returning an empty
// map for anything that is not a JSON object.
func decodeJSONObject(raw string) map[string]any {
	var out map[string]any
	if json.Unmarshal([]byte(raw), &out) != nil || out == nil {
		return map[string]any{}
	}
	return out
}

// consoleProxyEntry renders one bus event as the console's ProxyLogEntry. The
// Go bus carries request/alert/unpriced events rather than Python's proxy log
// lines, so each is summarized into the level/message the page displays.
func consoleProxyEntry(e logging.Event) map[string]any {
	level := "info"
	message := e.Type
	switch e.Type {
	case "alert":
		level = "warn"
		if m, ok := e.Data["message"].(string); ok && m != "" {
			message = m
		} else {
			message = "alert fired"
		}
	case "unpriced":
		level = "warn"
		message = "unpriced model"
		if group, ok := e.Data["group"].(string); ok && group != "" {
			message += ": " + group
		}
	case "request":
		status := int(numOr(e.Data["status"], 0))
		if status >= 500 {
			level = "error"
		} else if status >= 400 {
			level = "warn"
		}
		surface, _ := e.Data["surface"].(string)
		group, _ := e.Data["group"].(string)
		latency := int(numOr(e.Data["latency_ms"], 0))
		message = fmt.Sprintf("%s %s -> %d (%dms)", surface, group, status, latency)
	}
	out := map[string]any{
		"stream": "proxy", "ts": float64(e.Ts), "level": level, "message": message,
	}
	if rid, ok := e.Data["request_id"].(string); ok && rid != "" {
		out["request_id"] = rid
	}
	return out
}

func numOr(v any, def float64) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case float32:
		return float64(n)
	case int:
		return float64(n)
	case int64:
		return float64(n)
	}
	return def
}
