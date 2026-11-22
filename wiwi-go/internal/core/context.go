package core

import (
	"context"
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
	// WasStream marks a request served over SSE; the exporter distinguishes a
	// mid-stream failure from an ordinary error with it.
	WasStream bool

	SessionID      string
	Lane           string
	ForwardHeaders map[string]string

	// TranslationWarnings records wire-translation degradations (a dropped or
	// coerced field) so the log row carries them instead of losing them with
	// the response.
	TranslationWarnings []string

	mu             sync.Mutex
	DeferKeyCredit bool

	// cancelC carries the caller's cancellation (client hangup) so upstream
	// calls are torn down instead of draining into a dead socket.
	cancelC context.Context
	// ClientGone is set when the caller disconnected before the response
	// completed; the request keeps draining for stream_grace_drain_s so usage
	// is still billed.
	ClientGone bool
	// DepSlotReserved records which deployment holds an rpm/tpm reservation
	// for this request, so a failure can refund exactly that slot.
	DepSlotReserved any
	// RateBuckets records the limiter reservations taken at admission, so
	// finish can settle the estimate against real usage or refund it.
	RateBuckets []RateBucketReservation
}

// RateBucketReservation identifies one limiter reservation (bucket + the
// request identity that took it).
type RateBucketReservation struct {
	Bucket    string
	RequestID string
}

// SetCancel installs the caller's cancellation context for this request.
// Upstream calls derive from it via Context(), so a client hangup tears them
// down; the stream watchdogs derive from it too (their own timeout cancel is
// separate, on the watchdog).
func (c *RequestContext) SetCancel(ctx context.Context) {
	if ctx == nil {
		return
	}
	c.cancelC = ctx
}

// Context returns the base context for upstream calls. Callers that need to
// cancel the upstream on hangup use UpstreamContext.
func (c *RequestContext) Context() context.Context {
	if c != nil && c.cancelC != nil {
		return c.cancelC
	}
	return context.Background()
}

// MarkClientGone records a caller disconnect without cancelling the upstream
// drain, so the remaining stream is still consumed and billed.
func (c *RequestContext) MarkClientGone() {
	c.mu.Lock()
	c.ClientGone = true
	c.mu.Unlock()
}

// Cancelled reports whether the caller went away.
func (c *RequestContext) Cancelled() bool {
	if c == nil || c.cancelC == nil {
		return false
	}
	return c.cancelC.Err() != nil
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

// TPS reports output tokens/sec over the generation window only
// (first→last token), matching Python. A non-streaming request has no
// separable generation phase and reports 0.
func (c *RequestContext) TPS() float64 {
	if c.Usage == nil || c.Usage.CompletionTokens <= 0 ||
		c.FirstTokenAt == nil || c.LastTokenAt == nil {
		return 0
	}
	if secs := c.LastTokenAt.Sub(*c.FirstTokenAt).Seconds(); secs > 0.05 {
		return float64(c.Usage.CompletionTokens) / secs
	}
	return 0
}

// Done returns a channel closed when the caller disconnects, or nil when no
// caller context is installed (a nil channel blocks forever in select, which is
// exactly the "never cancelled" behavior wanted).
func (c *RequestContext) Done() <-chan struct{} {
	if c == nil || c.cancelC == nil {
		return nil
	}
	return c.cancelC.Done()
}
