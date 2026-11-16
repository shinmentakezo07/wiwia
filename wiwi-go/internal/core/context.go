package core

import (
	"sync"
	"time"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/providers"
)

type Surface string

const (
	SurfaceChat        Surface = "chat"
	SurfaceResponses   Surface = "responses"
	SurfaceMessages    Surface = "messages"
	SurfaceCompletions Surface = "completions"
)

type AuthInfo struct {
	KeyID     string
	KeyName   string
	IsMaster  bool
	MaxBudget float64
	Spend     float64
	Models    []string
	RPM       int
	TPM       int
	Role      string
	UserID    string
}

type AttemptRecord struct {
	Deployment       string `json:"deployment"`
	Provider         string `json:"provider"`
	ProviderKeyLabel string `json:"provider_key_label"`
	Status           string `json:"status"`
	LatencyMs        int    `json:"latency_ms"`
	Detail           string `json:"detail"`
	ModelID          string `json:"model_id"`
}

// RequestContext is the single mutable holder threaded through every stage.
type RequestContext struct {
	Surface   Surface
	IRReq     ir.Request
	Started   time.Time
	RequestID string
	Auth      *AuthInfo

	Group      string
	Deployment any
	Key        any
	Attempts   []AttemptRecord

	FirstTokenAt *time.Time
	LastTokenAt  *time.Time

	Usage          *ir.Usage
	Cost           float64
	Priced         bool
	EstTokens      int
	BudgetReserved float64

	CacheHit         bool
	ResponseCacheHit bool
	StopReason       string
	Status           int
	Error            *providers.WiwiError
	Metadata         map[string]any

	SessionID      string
	Lane           string
	ForwardHeaders map[string]string

	mu             sync.Mutex
	DeferKeyCredit bool
}

func (c *RequestContext) NoteAttempt(dep, provider, keyLabel, status string, latencyMs int, detail, modelID string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.Attempts = append(c.Attempts, AttemptRecord{
		Deployment: dep, Provider: provider, ProviderKeyLabel: keyLabel,
		Status: status, LatencyMs: latencyMs, Detail: detail, ModelID: modelID,
	})
}

func (c *RequestContext) LatencyMs() int {
	return int(time.Since(c.Started).Milliseconds())
}

func (c *RequestContext) TTFTMs() int {
	if c.FirstTokenAt == nil {
		return 0
	}
	return int(c.FirstTokenAt.Sub(c.Started).Milliseconds())
}
