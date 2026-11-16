package ir

import "strings"

// Registry of provider-hosted builtin tools. A builtin tool is executed by the
// provider itself (Anthropic web_search_*, OpenAI Responses web_search, Gemini
// google_search); the model never sees a function schema. The canonical name
// rides the IR; per-surface wire types are rendered at codec/adapter boundaries.

// BuiltinToolTypes maps canonical name -> surface -> wire tool type.
// A nil value means the surface has no representation (encoders drop it).
var BuiltinToolTypes = map[string]map[string]*string{
	"web_search": {
		"anthropic":        strp("web_search_20250305"),
		"openai_responses": strp("web_search"),
		"openai_chat":      nil,
		"openrouter":       strp("openrouter:web_search"),
		"gemini":           strp("google_search"),
	},
	"tool_search_bm25": {
		"anthropic":        strp("tool_search_tool_bm25_20251119"),
		"openai_responses": strp("tool_search"),
		"openai_chat":      nil,
		"openrouter":       nil,
		"gemini":           nil,
	},
	"tool_search_regex": {
		"anthropic":        strp("tool_search_tool_regex_20251119"),
		"openai_responses": strp("tool_search"),
		"openai_chat":      nil,
		"openrouter":       nil,
		"gemini":           nil,
	},
}

// BuiltinConfigKeys is the provider-common config subset carried in
// Tool.BuiltinConfig.
var BuiltinConfigKeys = []string{
	"max_uses", "allowed_domains", "blocked_domains", "user_location", "search_context_size",
}

const (
	WireTypeKey    = "_wire_type"
	WireSurfaceKey = "_wire_surface"
)

// BuiltinSurfaces lists the surfaces that can carry a builtin tool definition.
var BuiltinSurfaces = []string{"anthropic", "openai_responses", "openai_chat", "openrouter", "gemini"}

func strp(s string) *string { return &s }

var (
	reverse = map[string]map[string]string{}
	aliases = map[string]map[string]string{
		"openai_responses": {
			"web_search_preview":    "web_search",
			"web_search_2025_08_26": "web_search",
		},
	}
)

func init() {
	// First canonical to claim a wire type keeps it (declaration order in the
	// literal above is not preserved by a Go map, so we iterate a fixed order).
	order := []string{"web_search", "tool_search_bm25", "tool_search_regex"}
	for _, surface := range BuiltinSurfaces {
		rev := map[string]string{}
		for _, canonical := range order {
			if wt := BuiltinToolTypes[canonical][surface]; wt != nil && *wt != "" {
				if _, exists := rev[*wt]; !exists {
					rev[*wt] = canonical
				}
			}
		}
		reverse[surface] = rev
	}
}

// CanonicalFor maps a surface's wire tool type to the canonical builtin name.
func CanonicalFor(surface, wireType string) string {
	if surface == "" || wireType == "" {
		return ""
	}
	if hit, ok := reverse[surface][wireType]; ok {
		return hit
	}
	if hit, ok := aliases[surface][wireType]; ok {
		return hit
	}
	if surface == "anthropic" {
		for canonical, row := range BuiltinToolTypes {
			if family := row["anthropic"]; family != nil && *family != "" {
				prefix := (*family)[:strings.LastIndex(*family, "_")+1]
				if strings.HasPrefix(wireType, prefix) {
					return canonical
				}
			}
		}
	}
	return ""
}

// WireTypeFor maps a canonical builtin name to the surface's wire tool type.
func WireTypeFor(surface, canonical string) (string, bool) {
	row, ok := BuiltinToolTypes[canonical]
	if !ok {
		return "", false
	}
	wt := row[surface]
	if wt == nil || *wt == "" {
		return "", false
	}
	return *wt, true
}

// IsBuiltinName reports whether a tool-call name names a canonical builtin.
func IsBuiltinName(name string) bool {
	_, ok := BuiltinToolTypes[name]
	return ok
}

// PassthroughWireType returns the raw wire type to re-emit for a builtin the
// registry does not know, when the target surface is the one that declared it.
func PassthroughWireType(surface string, cfg map[string]any) string {
	if cfg == nil {
		return ""
	}
	if s, _ := cfg[WireSurfaceKey].(string); s != surface {
		return ""
	}
	raw, _ := cfg[WireTypeKey].(string)
	return raw
}

// BuiltinConfigSubset extracts the provider-common config subset.
func BuiltinConfigSubset(cfg map[string]any) map[string]any {
	if cfg == nil {
		return nil
	}
	out := map[string]any{}
	for _, k := range BuiltinConfigKeys {
		if v, ok := cfg[k]; ok {
			out[k] = v
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}
