package ir

// Canonical Internal Representation — the single source of truth for all
// translation. Inbound wire dialects decode into these types; provider adapters
// encode from them and decode back into them.

type Role string

const (
	RoleSystem    Role = "system"
	RoleUser      Role = "user"
	RoleAssistant Role = "assistant"
	RoleTool      Role = "tool"
)

type StopReason string

const (
	StopStop                  StopReason = "stop"
	StopLength                StopReason = "length"
	StopToolCall              StopReason = "tool_call"
	StopContentFilter         StopReason = "content_filter"
	StopPauseTurn             StopReason = "pause_turn"
	StopStopSequence          StopReason = "stop_sequence"
	StopContextWindowExceeded StopReason = "context_window_exceeded"
	StopCompaction            StopReason = "compaction"
)

// Message holds an ordered list of tagged content parts (see part.go).
type Message struct {
	Role  Role   `json:"role"`
	Parts []Part `json:"parts"`
}

type Tool struct {
	Name                 string           `json:"name"`
	Description          string           `json:"description"`
	ParametersJSONSchema map[string]any   `json:"parameters_json_schema"`
	Strict               *bool            `json:"strict,omitempty"`
	InputExamples        []map[string]any `json:"input_examples,omitempty"`
	CacheControl         map[string]any   `json:"cache_control,omitempty"`
	Builtin              *string          `json:"builtin,omitempty"`
	BuiltinConfig        map[string]any   `json:"builtin_config,omitempty"`
	DeferLoading         *bool            `json:"defer_loading,omitempty"`
}

type GenParams struct {
	Temperature            *float64        `json:"temperature,omitempty"`
	TopP                   *float64        `json:"top_p,omitempty"`
	MaxTokens              *int            `json:"max_tokens,omitempty"`
	Stop                   []string        `json:"stop,omitempty"`
	Seed                   *int            `json:"seed,omitempty"`
	N                      int             `json:"n"`
	ResponseFormat         *ResponseFormat `json:"response_format,omitempty"`
	ParallelToolCalls      *bool           `json:"parallel_tool_calls,omitempty"`
	DisableParallelToolUse *bool           `json:"disable_parallel_tool_use,omitempty"`
	ReasoningEffort        *string         `json:"reasoning_effort,omitempty"`
	ThinkingBudget         *int            `json:"thinking_budget,omitempty"`
	Effort                 *string         `json:"effort,omitempty"`
	ThinkingType           *string         `json:"thinking_type,omitempty"`
	TopK                   *int            `json:"top_k,omitempty"`
}

type ResponseFormat struct {
	Type       string         `json:"type"`
	JSONSchema map[string]any `json:"json_schema,omitempty"`
	Name       *string        `json:"name,omitempty"`
	Strict     *bool          `json:"strict,omitempty"`
}

// ToolChoice is a tagged union.
type ToolChoice interface{ isToolChoice() }

type ToolChoiceAuto struct{}

func (ToolChoiceAuto) isToolChoice() {}

type ToolChoiceNone struct{}

func (ToolChoiceNone) isToolChoice() {}

type ToolChoiceRequired struct{}

func (ToolChoiceRequired) isToolChoice() {}

type ToolChoiceNamed struct {
	Name string `json:"name"`
}

func (ToolChoiceNamed) isToolChoice() {}

// EffortBudgets maps a reasoning effort level to a conservative thinking-token
// budget. "none" and unknown levels mean thinking off.
var EffortBudgets = map[string]int{
	"minimal": 1024,
	"low":     1024,
	"medium":  8000,
	"high":    32000,
	"xhigh":   64000,
	"max":     64000,
}

func EffortToThinkingBudget(effort string) (int, bool) {
	b, ok := EffortBudgets[effort]
	return b, ok
}

// EffectiveReasoningEffort resolves the effective effort from all knobs, most
// specific first (mirrors the Python GenParams accessor).
func (g GenParams) EffectiveReasoningEffort() string {
	if g.ReasoningEffort != nil && *g.ReasoningEffort != "" {
		return *g.ReasoningEffort
	}
	if g.ThinkingType != nil && *g.ThinkingType == "disabled" {
		return "none"
	}
	if g.Effort != nil && *g.Effort != "" {
		return *g.Effort
	}
	if g.ThinkingBudget != nil {
		return thinkingBudgetToEffort(*g.ThinkingBudget)
	}
	if g.ThinkingType != nil && *g.ThinkingType == "enabled" {
		return "medium"
	}
	return ""
}

// EffectiveThinkingBudget resolves the token budget, or (false) for off.
func (g GenParams) EffectiveThinkingBudget() (int, bool) {
	if (g.ThinkingType != nil && *g.ThinkingType == "disabled") ||
		(g.ReasoningEffort != nil && *g.ReasoningEffort == "none") {
		return 0, false
	}
	if g.ThinkingBudget != nil {
		return *g.ThinkingBudget, true
	}
	for _, e := range []*string{g.ReasoningEffort, g.Effort} {
		if e != nil && *e != "" {
			if b, ok := EffortToThinkingBudget(*e); ok {
				return b, true
			}
		}
	}
	if g.ThinkingType != nil && *g.ThinkingType == "enabled" {
		return EffortBudgets["medium"], true
	}
	return 0, false
}

func thinkingBudgetToEffort(b int) string {
	switch {
	case b <= 0:
		return "none"
	case b <= 2048:
		return "low"
	case b <= 16000:
		return "medium"
	case b <= 48000:
		return "high"
	default:
		return "xhigh"
	}
}

type Request struct {
	Model                     string         `json:"model"`
	Messages                  []Message      `json:"messages"`
	Tools                     []Tool         `json:"tools"`
	ToolChoice                ToolChoice     `json:"tool_choice,omitempty"`
	GenParams                 GenParams      `json:"gen_params"`
	Stream                    bool           `json:"stream"`
	StreamOptionsIncludeUsage bool           `json:"stream_options_include_usage"`
	Extras                    map[string]any `json:"extras"`
}

type Usage struct {
	PromptTokens        int   `json:"prompt_tokens"`
	CompletionTokens    int   `json:"completion_tokens"`
	CachedTokens        int   `json:"cached_tokens"`
	ReasoningTokens     int   `json:"reasoning_tokens"`
	Estimated           bool  `json:"estimated"`
	CacheCreationTokens int   `json:"cache_creation_tokens"`
	CachedInPrompt      *bool `json:"cached_in_prompt,omitempty"`
}

func (u Usage) Total() int { return u.PromptTokens + u.CompletionTokens }

// AssistantTurn is the model's output as IR.
type AssistantTurn struct {
	Text         string           `json:"text"`
	Thinking     []ThinkingPart   `json:"thinking"`
	ToolCalls    []ToolUsePart    `json:"tool_calls"`
	StopReason   StopReason       `json:"stop_reason"`
	StopSequence *string          `json:"stop_sequence,omitempty"`
	Usage        Usage            `json:"usage"`
	ServerBlocks []map[string]any `json:"server_blocks,omitempty"`
	Logprobs     map[string]any   `json:"logprobs,omitempty"`
}

// Concrete output part structs (IR side of the assistant turn).
type ToolUsePart struct {
	ID           string         `json:"id"`
	Name         string         `json:"name"`
	Args         map[string]any `json:"args"`
	RawArgs      string         `json:"raw_args,omitempty"`
	Builtin      string         `json:"builtin,omitempty"`
	BlockType    string         `json:"block_type"`
	CacheControl map[string]any `json:"cache_control,omitempty"`
}

type ThinkingPart struct {
	Text             string `json:"text"`
	Signature        string `json:"signature,omitempty"`
	BlockType        string `json:"block_type"`
	Data             string `json:"data,omitempty"`
	EncryptedContent string `json:"encrypted_content,omitempty"`
}
