package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"wiwi-go/internal/cost"
	"wiwi-go/internal/ir"
	"wiwi-go/internal/providers"
	"wiwi-go/internal/router"
	"wiwi-go/internal/streaming"
	"wiwi-go/internal/telemetry"
)

// Gateway executes one IR request: router -> adapter -> upstream, with retries,
// key/deployment failover, fallback groups, pricing, and stream pumping.
type Gateway struct {
	Router *router.Router
	Cost   *cost.Engine

	NumRetries   int
	AllowedFails int
	// StreamResume enables mid-stream failover with partial output prepended.
	StreamResume bool
	// LoopLimit aborts degenerate repetition (0 disables).
	LoopLimit int
	// DropParams governs unmodelled caller extras: when true (the default)
	// adapters forward only the keys their wire documents; when false they
	// forward every extra raw. Mirrors wiwi_settings.drop_params.
	DropParams bool
	// Tracer records request spans when telemetry is enabled.
	Tracer *telemetry.Tracer
	// StreamCoalesce merges TextDeltas under backpressure (opt-in).
	StreamCoalesce   bool
	CoalesceMaxBytes int
	CoalesceMaxMS    float64
	// Streaming watchdogs + resume (router_settings).
	StreamIdleTimeoutS       float64
	StreamFirstChunkTimeoutS float64
	StreamResumeMode         string
	ResumeMaxRetries         int
}

func New(rt *router.Router, ce *cost.Engine) *Gateway {
	g := &Gateway{Router: rt, Cost: ce, NumRetries: rt.NumRetries(), LoopLimit: 100, DropParams: true}
	rt.ApplyThresholds()
	rt.InitLaneCaps()
	return g
}

func toWiwiError(err error, provider string) *providers.WiwiError {
	var we *providers.WiwiError
	if errors.As(err, &we) {
		return we
	}
	return &providers.WiwiError{Status: 502, EType: "api_error", Message: err.Error(), Retryable: true}
}

func timeoutOf(d *router.Deployment) float64 {
	if d.Timeout != nil && *d.Timeout > 0 {
		return *d.Timeout
	}
	if d.Provider.TimeoutS > 0 {
		return d.Provider.TimeoutS
	}
	return 120
}

func mergeHeaders(base map[string]string, extra map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range base {
		out[k] = v
	}
	for k, v := range extra {
		out[k] = v
	}
	return out
}

func deploymentParams(d *router.Deployment, dropParams bool) map[string]any {
	out := map[string]any{"drop_params": dropParams}
	if d.MaxTokens != nil {
		out["max_tokens"] = *d.MaxTokens
	}
	for k, v := range d.ExtraBody {
		out[k] = v
	}
	if d.PromptCache {
		out["prompt_cache"] = true
		if d.PromptCacheMinTokens != nil {
			out["prompt_cache_min_tokens"] = *d.PromptCacheMinTokens
		}
	}
	return out
}

// candidates builds the ordered group list including fallbacks.
func (g *Gateway) candidates(group string, contextOverflow bool) []string {
	seen := map[string]bool{}
	var out []string
	var add func(string)
	add = func(grp string) {
		grp = g.Router.ResolveGroup(grp)
		if grp == "" || seen[grp] || len(g.Router.Deployments(grp)) == 0 {
			return
		}
		seen[grp] = true
		out = append(out, grp)
		for _, fb := range g.Router.Fallbacks(grp, contextOverflow) {
			add(fb)
		}
	}
	add(group)
	return out
}

func (g *Gateway) callOnce(ctx *RequestContext, dep *router.Deployment, key *router.ProviderKey) (*ir.AssistantTurn, *providers.WiwiError) {
	adapter, err := providers.FreshAdapter(dep.Provider.Type)
	if err != nil {
		return nil, &providers.WiwiError{Status: 500, EType: "api_error", Message: err.Error()}
	}
	req := ctx.IRReq
	req.Model = dep.Model
	body, err := adapter.EncodeRequest(req, dep.Model, deploymentParams(dep, g.DropParams))
	if err != nil {
		return nil, &providers.WiwiError{Status: 500, EType: "api_error", Message: err.Error()}
	}
	headers := mergeHeaders(adapter.Headers(providers.ProviderKeyRef{Label: key.Label, Secret: key.Secret}), dep.Provider.ExtraHeaders)
	for k, v := range dep.ExtraHeaders {
		headers[k] = v
	}
	for k, v := range ctx.ForwardHeaders {
		headers[k] = v
	}
	if dep.Provider.Type == "ifm" && ctx.SessionID != "" {
		headers["X-Session-ID"] = ctx.SessionID
	}
	url := adapter.BuildURL(dep.Provider.BaseURL, dep.Model, false)
	respBody, status, err := providers.DoRequest(url, headers, body, timeoutOf(dep))
	if err != nil {
		return nil, &providers.WiwiError{Status: 502, EType: "api_connection_error", Message: err.Error(), Retryable: true}
	}
	if status != http.StatusOK {
		return nil, providers.ErrorFromProviderStatus(status, respBody, dep.Provider.Type)
	}
	turn, err := adapter.DecodeResponse(status, respBody)
	if err != nil {
		return nil, &providers.WiwiError{Status: 502, EType: "api_error", Message: err.Error(), Retryable: true}
	}
	return &turn, nil
}

// Complete runs a non-streaming request through the retry/failover ladder.
func (g *Gateway) Complete(ctx *RequestContext) (*ir.AssistantTurn, *providers.WiwiError) {
	span := g.Tracer.Start("wiwi.complete", map[string]any{"group": ctx.Group, "surface": string(ctx.Surface)})
	defer func() {
		if ctx.Error != nil {
			span.End(ctx.Error)
		} else {
			span.End(nil)
		}
	}()
	prefix := prefixOf(ctx.IRReq)
	turn, we := g.completeGroups(ctx, g.candidates(ctx.Group, false), prefix)
	if we != nil && we.EType == "context_window_exceeded" {
		if turn2, we2 := g.completeGroups(ctx, g.candidates(ctx.Group, true), prefix); we2 == nil {
			return turn2, nil
		} else {
			we = we2
		}
	}
	ctx.Error = we
	return turn, we
}

func (g *Gateway) completeGroups(ctx *RequestContext, groups []string, prefix string) (*ir.AssistantTurn, *providers.WiwiError) {
	var lastErr *providers.WiwiError
	for _, grp := range groups {
		excludeDeps := map[string]bool{}
		for attempt := 0; attempt <= g.NumRetries; attempt++ {
			dep, err := g.Router.PickDeploymentFor(grp, excludeDeps, ctx.SessionID, prefix)
			if err != nil {
				break
			}
			if !g.Router.ReserveSlot(dep, ctx.Lane) {
				excludeDeps[dep.Name()] = true
				continue
			}
			ctx.Deployment = dep
			keyExclude := map[string]bool{}
			for keyAttempt := 0; keyAttempt < 2; keyAttempt++ {
				key, err := dep.PickKey(keyExclude)
				if err != nil {
					break
				}
				ctx.Key = key
				start := time.Now()
				turn, we := g.callOnce(ctx, dep, key)
				if we == nil {
					dep.OnKeyResult(key, 200, nil)
					dep.OnDeploymentResult(200, nil)
					ctx.NoteAttempt(dep.Name(), dep.Provider.Name, key.Label, "ok", int(time.Since(start).Milliseconds()), "", dep.Model)
					g.price(ctx, dep, turn.Usage)
					g.Router.RecordAffinity(ctx.SessionID, prefix, dep.Name())
					g.Router.ReleaseSlot(dep, ctx.Lane)
					return turn, nil
				}
				lastErr = we
				dep.OnKeyResult(key, we.Status, we)
				dep.OnDeploymentResult(we.Status, we)
				ctx.NoteAttempt(dep.Name(), dep.Provider.Name, key.Label, we.EType, int(time.Since(start).Milliseconds()), we.Message, dep.Model)
				if !we.Retryable || (g.Router.FailoverStandard() && !standardRetryable(we.Status)) {
					g.Router.ReleaseSlot(dep, ctx.Lane)
					return nil, we
				}
				keyExclude[key.Label] = true
			}
			g.Router.ReleaseSlot(dep, ctx.Lane)
			excludeDeps[dep.Name()] = true
		}
	}
	if lastErr == nil {
		lastErr = &providers.WiwiError{Status: 503, EType: "service_unavailable", Message: fmt.Sprintf("no healthy deployment for group %q", ctx.Group), Retryable: true}
	}
	return nil, lastErr
}

func (g *Gateway) price(ctx *RequestContext, dep *router.Deployment, u ir.Usage) {
	ctx.Usage = &u
	if g.Cost != nil {
		c, priced := g.Cost.Compute(dep.Model, u)
		ctx.Cost = c
		ctx.Priced = priced
	}
}

// Stream pumps a streaming request into a delta channel. Errors are delivered
// as a terminal StreamError delta. The caller encodes the deltas.
func (g *Gateway) Stream(ctx *RequestContext) <-chan streaming.Delta {
	out := make(chan streaming.Delta, 64)
	span := g.Tracer.Start("wiwi.stream", map[string]any{"group": ctx.Group, "surface": string(ctx.Surface)})
	go func() {
		defer close(out)
		defer func() {
			if ctx.Error != nil {
				span.End(ctx.Error)
			} else {
				span.End(nil)
			}
		}()
		groups := g.candidates(ctx.Group, false)
		var lastErr *providers.WiwiError
		var tape *streaming.StreamTape
		suppressStart := false
		resumes := 0
		for _, grp := range groups {
			excludeDeps := map[string]bool{}
			for attempt := 0; attempt <= g.NumRetries; attempt++ {
				prefix := prefixOf(ctx.IRReq)
				dep, err := g.Router.PickDeploymentFor(grp, excludeDeps, ctx.SessionID, prefix)
				if err != nil {
					break
				}
				if !g.Router.ReserveSlot(dep, ctx.Lane) {
					excludeDeps[dep.Name()] = true
					continue
				}
				ctx.Deployment = dep
				key, err := dep.PickKey(nil)
				if err != nil {
					g.Router.ReleaseSlot(dep, ctx.Lane)
					excludeDeps[dep.Name()] = true
					continue
				}
				ctx.Key = key
				ok, we, contentSeen, t := g.pump(ctx, dep, key, out, tape, suppressStart)
				g.Router.ReleaseSlot(dep, ctx.Lane)
				if t != nil {
					tape = t
				}
				if ok {
					g.Router.RecordAffinity(ctx.SessionID, prefix, dep.Name())
					return
				}
				lastErr = we
				if we != nil {
					dep.OnKeyResult(key, we.Status, we)
					dep.OnDeploymentResult(we.Status, we)
				}
				// Mid-stream resume: content already flowed and no tool call is
				// open, so retry on a fallback with the partial output prepended.
				if contentSeen && g.resumeEnabled() && resumes < g.ResumeMaxRetries &&
					tape != nil && tape.Text() != "" && len(tape.ToolCalls()) == 0 {
					ctx.IRReq.Messages = append(ctx.IRReq.Messages, tape.Continuation()...)
					suppressStart = true
					resumes++
					excludeDeps[dep.Name()] = true
					continue
				}
				if we != nil && (!we.Retryable || (g.Router.FailoverStandard() && !standardRetryable(we.Status))) {
					out <- streaming.StreamError{Message: we.Message, Status: we.Status, EType: we.EType}
					return
				}
				excludeDeps[dep.Name()] = true
			}
		}
		if lastErr == nil {
			lastErr = &providers.WiwiError{Status: 503, EType: "service_unavailable", Message: "no healthy deployment", Retryable: true}
		}
		out <- streaming.StreamError{Message: lastErr.Message, Status: lastErr.Status, EType: lastErr.EType}
	}()
	return out
}

func (g *Gateway) resumeEnabled() bool {
	return g.StreamResumeMode != "" && g.StreamResumeMode != "off"
}

func standardRetryable(status int) bool {
	switch status {
	case 408, 429, 500, 502, 503, 504, 529:
		return true
	}
	return false
}

// pump runs one streaming attempt. It returns (true, nil) if the stream
// completed (or was legitimately aborted with a Finish), and (false, err) when
// the attempt failed before any content flowed and a retry is worthwhile.
func (g *Gateway) pump(ctx *RequestContext, dep *router.Deployment, key *router.ProviderKey, out chan<- streaming.Delta, tapeIn *streaming.StreamTape, suppressStart bool) (bool, *providers.WiwiError, bool, *streaming.StreamTape) {
	adapter, err := providers.FreshAdapter(dep.Provider.Type)
	if err != nil {
		return false, &providers.WiwiError{Status: 500, EType: "api_error", Message: err.Error()}, false, tapeIn
	}
	req := ctx.IRReq
	req.Model = dep.Model
	body, err := adapter.EncodeRequest(req, dep.Model, deploymentParams(dep, g.DropParams))
	if err != nil {
		return false, &providers.WiwiError{Status: 500, EType: "api_error", Message: err.Error()}, false, tapeIn
	}
	headers := mergeHeaders(adapter.Headers(providers.ProviderKeyRef{Label: key.Label, Secret: key.Secret}), dep.Provider.ExtraHeaders)
	for k, v := range dep.ExtraHeaders {
		headers[k] = v
	}
	for k, v := range ctx.ForwardHeaders {
		headers[k] = v
	}
	if dep.Provider.Type == "ifm" && ctx.SessionID != "" {
		headers["X-Session-ID"] = ctx.SessionID
	}
	watch := newStreamWatchdog(g.StreamFirstChunkTimeoutS, g.StreamIdleTimeoutS)
	defer watch.stop()
	resp, err := providers.DoStreamCtx(watch.ctx, adapter.BuildURL(dep.Provider.BaseURL, dep.Model, true), headers, body)
	if err != nil {
		if watch.timedOut() {
			return false, &providers.WiwiError{Status: 504, EType: "timeout", Message: "upstream produced no data before the first-chunk timeout", Retryable: true}, false, tapeIn
		}
		return false, &providers.WiwiError{Status: 502, EType: "api_connection_error", Message: err.Error(), Retryable: true}, false, tapeIn
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		buf := make([]byte, 4096)
		n, _ := resp.Body.Read(buf)
		return false, providers.ErrorFromProviderStatus(resp.StatusCode, buf[:n], dep.Provider.Type), false, tapeIn
	}

	started := false
	contentSeen := false
	tape := tapeIn
	if tape == nil && g.resumeEnabled() {
		tape = streaming.NewStreamTape()
	}
	detector := streaming.NewLoopDetector(g.LoopLimit)
	usage := streaming.UsageFinal{}
	finished := false

	forward := func(d streaming.Delta) bool {
		if tape != nil {
			tape.Observe(d)
		}
		switch d.(type) {
		case streaming.StreamStart:
			started = true
			if suppressStart {
				return true // resume: the client already saw the first start
			}
		case streaming.TextDelta, streaming.ToolCallOpen:
			contentSeen = true
			watch.toIdle()
			if ctx.FirstTokenAt == nil {
				now := time.Now()
				ctx.FirstTokenAt = &now
			}
		case streaming.UsageFinal:
			usage = d.(streaming.UsageFinal)
		case streaming.Finish:
			finished = true
		}
		select {
		case out <- d:
			return true
		case <-time.After(30 * time.Second):
			return false
		}
	}

	var coal *streaming.Coalescer
	if g.StreamCoalesce {
		coal = streaming.NewCoalescer(g.CoalesceMaxBytes, time.Duration(g.CoalesceMaxMS*float64(time.Millisecond)))
	}
	send := func(d streaming.Delta) bool {
		if coal != nil {
			switch d.(type) {
			case streaming.TextDelta:
				if merged := coal.Push(d); merged != nil {
					return forward(merged)
				}
				return true
			case streaming.Finish, streaming.StreamEnd, streaming.UsageFinal, streaming.StreamError:
				if merged := coal.Flush(); merged != nil {
					if !forward(merged) {
						return false
					}
				}
			}
		}
		return forward(d)
	}

	parseErr := streaming.Parse(resp.Body, func(ev streaming.Event) bool {
		watch.kick()
		if ev.Data == "" && ev.Event == "" {
			return true
		}
		deltas, derr := adapter.DecodeStreamEvent(ev.Event, ev.Data)
		if derr != nil {
			return true
		}
		for _, d := range deltas {
			if uf, ok := d.(streaming.UsageFinal); ok {
				usage = uf // capture; the enriched final is emitted once at the end
				continue
			}
			if _, ok := d.(streaming.StreamEnd); ok {
				return false
			}
			if detector.Observe(d) {
				send(streaming.Finish{StopReason: ir.StopStop})
				send(streaming.StreamEnd{})
				return false
			}
			if !send(d) {
				return false
			}
		}
		return true
	})

	if watch.timedOut() {
		return false, &providers.WiwiError{Status: 504, EType: "timeout", Message: "upstream stream stalled", Retryable: true}, contentSeen, tape
	}
	if !started && !contentSeen && parseErr != nil {
		return false, &providers.WiwiError{Status: 502, EType: "api_connection_error", Message: parseErr.Error(), Retryable: true}, contentSeen, tape
	}
	if !finished {
		send(streaming.Finish{StopReason: ir.StopStop})
	}
	u := ir.Usage{
		PromptTokens: usage.Prompt, CompletionTokens: usage.Output,
		CachedTokens: usage.Cached, ReasoningTokens: usage.Reasoning,
		CacheCreationTokens: usage.CacheCreation, Estimated: usage.Estimated,
	}
	g.price(ctx, dep, u)
	if !usage.Estimated {
		send(streaming.UsageFinal{Prompt: usage.Prompt, Cached: usage.Cached, Reasoning: usage.Reasoning, Output: usage.Output, CacheCreation: usage.CacheCreation, Cost: ctx.Cost})
	}
	send(streaming.StreamEnd{})
	dep.OnKeyResult(key, 200, nil)
	dep.OnDeploymentResult(200, nil)
	return true, nil, contentSeen, tape
}

// streamWatchdog cancels the upstream request when no data arrives within the
// first-chunk or inter-chunk budget.
type streamWatchdog struct {
	ctx      context.Context
	cancel   context.CancelFunc
	mu       sync.Mutex
	timer    *time.Timer
	first    time.Duration
	idle     time.Duration
	idleMode bool
	expired  bool
}

func newStreamWatchdog(firstS, idleS float64) *streamWatchdog {
	if firstS <= 0 {
		firstS = 300
	}
	if idleS <= 0 {
		idleS = 30
	}
	ctx, cancel := context.WithCancel(context.Background())
	w := &streamWatchdog{
		ctx: ctx, cancel: cancel,
		first: time.Duration(firstS * float64(time.Second)),
		idle:  time.Duration(idleS * float64(time.Second)),
	}
	w.timer = time.AfterFunc(w.first, func() {
		w.mu.Lock()
		w.expired = true
		w.mu.Unlock()
		cancel()
	})
	return w
}

func (w *streamWatchdog) kick() {
	w.mu.Lock()
	if w.timer != nil && !w.expired {
		if w.idleMode {
			w.timer.Reset(w.idle)
		} else {
			w.timer.Reset(w.first)
		}
	}
	w.mu.Unlock()
}

func (w *streamWatchdog) toIdle() {
	w.mu.Lock()
	w.idleMode = true
	if w.timer != nil && !w.expired {
		w.timer.Reset(w.idle)
	}
	w.mu.Unlock()
}

func (w *streamWatchdog) timedOut() bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	return w.expired
}

func (w *streamWatchdog) stop() {
	w.mu.Lock()
	if w.timer != nil {
		w.timer.Stop()
	}
	w.mu.Unlock()
	w.cancel()
}

// prefixOf derives a stable prompt-prefix affinity key from the system prompt
// and the tool definitions.
func prefixOf(req ir.Request) string {
	var sys strings.Builder
	for _, m := range req.Messages {
		if m.Role != ir.RoleSystem {
			continue
		}
		for _, p := range m.Parts {
			if p.Type == ir.PartText {
				sys.WriteString(p.Str("text"))
			}
		}
	}
	tools, _ := json.Marshal(req.Tools)
	return router.PrefixHash(sys.String(), string(tools))
}
