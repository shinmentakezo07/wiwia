package server

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"

	"wiwi-go/internal/cache"
	"wiwi-go/internal/core"
	"wiwi-go/internal/ir"
	"wiwi-go/internal/providers"
	"wiwi-go/internal/streaming"
	"wiwi-go/internal/wire"
)

func authOf(c *gin.Context) *core.AuthInfo {
	if v, ok := c.Get("auth"); ok {
		if ai, ok := v.(*core.AuthInfo); ok {
			return ai
		}
	}
	return nil
}

// prepare decodes the inbound body, resolves the model, and enforces
// allowlist + rate limits. Returns a fully-populated RequestContext or writes
// the error and returns nil.
func (a *App) prepare(c *gin.Context, surface core.Surface, body map[string]any) *core.RequestContext {
	ai := authOf(c)
	irReq, err := wire.DecodeRequest(wire.Surface(surface), body)
	if err != nil {
		a.writeErr(c, surface, 400, "invalid_request_error", err.Error())
		return nil
	}
	if len(ai.Models) > 0 && !modelAllowed(ai.Models, irReq.Model) {
		a.writeErr(c, surface, 403, "permission_error", "model not allowed for this key")
		return nil
	}
	group := a.State.Router.ResolveGroup(irReq.Model)
	if !a.State.Router.HasGroup(group) {
		a.writeErr(c, surface, 404, "not_found_error", "model not found: "+irReq.Model)
		return nil
	}
	// rate limit (per key) + global
	bucket := ai.KeyID
	if ai.IsMaster {
		bucket = "master"
	}
	if ok, retry := a.State.Limiter.Allow("key:"+bucket, ai.RPM, ai.TPM, 0); !ok {
		c.Header("Retry-After", strconv.Itoa(int(retry)+1))
		a.writeErr(c, surface, 429, "rate_limit_error", "rate limit exceeded")
		return nil
	}
	if ok, retry := a.State.Limiter.Allow("global", a.State.Router.GlobalRPM(), a.State.Router.GlobalTPM(), 0); !ok {
		c.Header("Retry-After", strconv.Itoa(int(retry)+1))
		a.writeErr(c, surface, 429, "rate_limit_error", "gateway rate limit exceeded")
		return nil
	}
	// unpriced-model policy: reject before serving when configured to fail closed.
	if a.State.Cfg.General.UnpricedModelPolicy == "reject" {
		priced := false
		for _, d := range a.State.Router.Deployments(group) {
			if _, ok := a.State.Cost.Compute(d.Model, ir.Usage{}); ok {
				priced = true
				break
			}
		}
		if !priced {
			a.writeErr(c, surface, 503, "service_unavailable", "model is not priced; ask an admin to add a price")
			return nil
		}
	}
	ctx := &core.RequestContext{
		Surface: surface, IRReq: irReq, Started: time.Now(),
		RequestID: requestID(c), Auth: ai, Group: group,
		Metadata: map[string]any{}, ForwardHeaders: forwardHeaders(c),
		SessionID: c.GetHeader("x-wiwi-session-id"),
		Lane:      c.GetHeader("x-wiwi-lane"),
	}
	// budget reservation (a conservative estimate)
	if !ai.IsMaster && ai.MaxBudget > 0 {
		est := 0.01
		if err := a.State.Auth.ReserveBudget(ai.KeyID, est); err != nil {
			a.writeErr(c, surface, 402, "budget_exceeded", "budget exceeded")
			return nil
		}
		ctx.BudgetReserved = est
	}
	return ctx
}

func modelAllowed(allowed []string, model string) bool {
	for _, m := range allowed {
		if m == model || m == "*" {
			return true
		}
	}
	return false
}

func requestID(c *gin.Context) string {
	if v, ok := c.Get("request_id"); ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

func forwardHeaders(c *gin.Context) map[string]string {
	out := map[string]string{}
	for _, h := range []string{"anthropic-beta", "anthropic-version", "openai-beta", "x-session-id"} {
		if v := c.GetHeader(h); v != "" {
			out[h] = v
		}
	}
	return out
}

// finish settles budget, records usage, logs, and updates totals.
func (a *App) finish(ctx *core.RequestContext, status int, e *providers.WiwiError) {
	ctx.Status = status
	ctx.Error = e
	cost := ctx.Cost
	if ctx.Auth != nil && !ctx.Auth.IsMaster {
		if a.State.SpendBuffer != nil {
			a.State.SpendBuffer.Add(ctx.Auth.KeyID, ctx.BudgetReserved, cost)
		} else {
			_ = a.State.Auth.Settle(ctx.Auth.KeyID, ctx.BudgetReserved, cost)
		}
	}
	prompt, completion := 0, 0
	if ctx.Usage != nil {
		prompt, completion = ctx.Usage.PromptTokens, ctx.Usage.CompletionTokens
	}
	isErr := e != nil || status >= 400
	if ctx.Usage != nil && !ctx.Priced {
		ctx.Metadata["unpriced"] = true
		a.State.Bus.Publish("unpriced", map[string]any{"request_id": ctx.RequestID, "group": ctx.Group})
	}
	a.State.Totals.AddRequest(prompt, completion, cost, isErr)
	if isErr {
		a.State.Metrics.Inc("wiwi_requests_failed_total", 1)
	} else {
		a.State.Metrics.Inc("wiwi_requests_success_total", 1)
	}
	a.logRequest(ctx)
	a.evaluateAlerts(ctx)
	providerName, modelID, keyLabel := "", "", ""
	if n := len(ctx.Attempts); n > 0 {
		last := ctx.Attempts[n-1]
		providerName, modelID, keyLabel = last.Provider, last.ModelID, last.ProviderKeyLabel
	}
	keyID, keyAlias := "", ""
	if ctx.Auth != nil {
		keyID, keyAlias = ctx.Auth.KeyID, ctx.Auth.KeyName
	}
	a.State.Bus.Publish("request", map[string]any{
		"stream": "request", "ts": time.Now().Unix(),
		"request_id": ctx.RequestID, "surface": string(ctx.Surface),
		"group": ctx.Group, "model_group": ctx.Group,
		"provider": providerName, "model_id": modelID,
		"key_id": keyID, "key_alias": keyAlias,
		"provider_key_label": keyLabel,
		"status":             status, "cost": cost,
		"prompt_tokens": prompt, "completion_tokens": completion,
		"tok_in": prompt, "tok_out": completion,
		"tps":     ctx.TPS(),
		"ttft_ms": ctx.TTFTMs(), "latency_ms": ctx.LatencyMs(),
		"was_stream": false,
	})
}

func (a *App) logRequest(ctx *core.RequestContext) {
	if a.State.Store == nil {
		return
	}
	attempts, _ := json.Marshal(ctx.Attempts)
	var errText string
	if ctx.Error != nil {
		errText = ctx.Error.Message
	}
	prompt, completion, cached, reasoning := 0, 0, 0, 0
	if ctx.Usage != nil {
		prompt = ctx.Usage.PromptTokens
		completion = ctx.Usage.CompletionTokens
		cached = ctx.Usage.CachedTokens
		reasoning = ctx.Usage.ReasoningTokens
	}
	modelID := ""
	if d, ok := ctx.Deployment.(interface{ ModelName() string }); ok {
		modelID = d.ModelName()
	}
	tps := ctx.TPS()
	keyID := ""
	if ctx.Auth != nil {
		keyID = ctx.Auth.KeyID
	}
	// StorePromptsInSpendLogs gates the raw prompt in request_body; the
	// Python schema has no prompt_text column, so the text rides the
	// documented column instead.
	requestBody := any(nil)
	if a.State.Cfg.Wiwi.StorePromptsInSpendLogs {
		if t := lastUserText(ctx); t != "" {
			requestBody = t
		}
	}
	_, _ = a.State.Store.DB().Exec(a.State.Store.Rebind(
		`INSERT INTO request_logs(request_id,ts,surface,key_alias,key_id,model_group,provider,provider_key_label,
		 model_id,status,error_code,error_message,tok_in,tok_cached,tok_cache_creation,tok_reasoning,tok_out,
		 usage_estimated,tps,ttft_ms,latency_ms,cost,was_stream,cache_hit,cache_savings,response_cache_hit,
		 attempts,translation_warnings,request_body,response_body)
		 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`),
		ctx.RequestID, time.Now().Unix(), string(ctx.Surface), "", keyID, ctx.Group,
		providerName(ctx), "", modelID, ctx.Status, "", errText,
		prompt, cached, 0, reasoning, completion, 0, tps, ctx.TTFTMs(), ctx.LatencyMs(),
		ctx.Cost, 0, 0, 0.0, 0, string(attempts), "[]", requestBody, nil)
}

func providerName(ctx *core.RequestContext) string {
	if p, ok := ctx.Deployment.(interface{ ProviderName() string }); ok {
		return p.ProviderName()
	}
	return ""
}

// ---- chat completions ----

func (a *App) handleChatCompletions(c *gin.Context) {
	body, err := readBody(c)
	if err != nil {
		a.writeErr(c, core.SurfaceChat, 400, "invalid_request_error", err.Error())
		return
	}
	ctx := a.prepare(c, core.SurfaceChat, body)
	if ctx == nil {
		return
	}
	if ctx.IRReq.Stream {
		a.serveStream(c, ctx, core.SurfaceChat, body)
		return
	}
	a.serveComplete(c, ctx, core.SurfaceChat, body)
}

// ---- legacy completions ----

func (a *App) handleLegacyCompletions(c *gin.Context) {
	body, err := readBody(c)
	if err != nil {
		a.writeErr(c, core.SurfaceCompletions, 400, "invalid_request_error", err.Error())
		return
	}
	ctx := a.prepare(c, core.SurfaceCompletions, body)
	if ctx == nil {
		return
	}
	if ctx.IRReq.Stream {
		a.serveStream(c, ctx, core.SurfaceCompletions, body)
		return
	}
	a.serveComplete(c, ctx, core.SurfaceCompletions, body)
}

// ---- messages ----

func (a *App) handleMessages(c *gin.Context) {
	body, err := readBody(c)
	if err != nil {
		a.writeErr(c, core.SurfaceMessages, 400, "invalid_request_error", err.Error())
		return
	}
	ctx := a.prepare(c, core.SurfaceMessages, body)
	if ctx == nil {
		return
	}
	if ctx.IRReq.Stream {
		a.serveStream(c, ctx, core.SurfaceMessages, body)
		return
	}
	a.serveComplete(c, ctx, core.SurfaceMessages, body)
}

// ---- responses ----

func (a *App) handleResponses(c *gin.Context) {
	body, err := readBody(c)
	if err != nil {
		a.writeErr(c, core.SurfaceResponses, 400, "invalid_request_error", err.Error())
		return
	}
	ctx := a.prepare(c, core.SurfaceResponses, body)
	if ctx == nil {
		return
	}
	if prev, _ := body["previous_response_id"].(string); prev != "" {
		if stored, ok := a.State.Responses.Get(prev, keyID(ctx.Auth)); ok {
			msgs := responseHistoryMessages(stored, ctx.IRReq.Model)
			ctx.IRReq.Messages = append(msgs, ctx.IRReq.Messages...)
		}
	}
	if ctx.IRReq.Stream {
		a.serveStream(c, ctx, core.SurfaceResponses, body)
		return
	}
	a.serveComplete(c, ctx, core.SurfaceResponses, body)
}

func (a *App) handleGetResponse(c *gin.Context) {
	ai := authOf(c)
	id := c.Param("id")
	body, ok := a.State.Responses.Get(id, keyID(ai))
	if !ok {
		a.writeErr(c, core.SurfaceResponses, 404, "not_found_error", "response not found")
		return
	}
	delete(body, "_input")
	c.JSON(http.StatusOK, body)
}

func (a *App) handleDeleteResponse(c *gin.Context) {
	ai := authOf(c)
	if !a.State.Responses.Delete(c.Param("id"), keyID(ai)) {
		a.writeErr(c, core.SurfaceResponses, 404, "not_found_error", "response not found")
		return
	}
	c.JSON(http.StatusOK, gin.H{"id": c.Param("id"), "object": "response.deleted", "deleted": true})
}

func keyID(ai *core.AuthInfo) string {
	if ai == nil {
		return "anonymous"
	}
	return ai.KeyID
}

// ---- shared execution ----

func (a *App) serveComplete(c *gin.Context, ctx *core.RequestContext, surface core.Surface, body map[string]any) {
	if a.State.Cfg.Wiwi.PerfTimings {
		defer func() {
			c.Header("x-wiwi-timings", fmt.Sprintf("total=%d", ctx.LatencyMs()))
		}()
	}
	// response cache (non-streaming, deterministic only)
	cacheKey := ""
	if a.cacheable(ctx, c) {
		cacheKey = cacheKeyFor(surface, ctx, c.GetHeader("anthropic-beta"))
		if b, ok := a.State.Cache.Get(cacheKey); ok {
			ctx.ResponseCacheHit = true
			a.finish(ctx, 200, nil)
			hit := rebaseHit(b, surface)
			if surface == core.SurfaceResponses && responseStoreEnabled(body) {
				var stored map[string]any
				if json.Unmarshal(hit, &stored) == nil {
					stored["_input"] = body["input"]
					if id, ok := stored["id"].(string); ok {
						_ = a.State.Responses.Save(id, keyID(ctx.Auth), stored)
					}
				}
			}
			c.Data(http.StatusOK, "application/json", hit)
			return
		}
	}
	// Singleflight: identical concurrent non-streaming requests share one
	// upstream call. Joiners are logged with the shared usage at cost=0.
	if a.State.Cfg.Wiwi.SingleflightEnabled {
		sfKey := cacheKeyFor(surface, ctx, c.GetHeader("anthropic-beta"))
		res, joined, err := a.State.Flight.DoResult(sfKey, func() (cache.Result, error) {
			turn, we := a.State.Gateway.Complete(ctx)
			if we != nil {
				return cache.Result{}, we
			}
			resp := wire.EncodeResponse(wire.Surface(surface), *turn, ctx.IRReq.Model, ctx.IRReq)
			if surface == core.SurfaceResponses && responseStoreEnabled(body) {
				stored := cloneMap(resp)
				stored["_input"] = body["input"]
				if id, ok := resp["id"].(string); ok {
					_ = a.State.Responses.Save(id, keyID(ctx.Auth), stored)
				}
			}
			b, jerr := json.Marshal(resp)
			return cache.Result{Payload: b, Usage: turn.Usage, Cost: ctx.Cost}, jerr
		})
		if err != nil {
			we := asWiwiError(err)
			a.finish(ctx, we.Status, we)
			a.writeWiwiErr(c, surface, we)
			return
		}
		if joined {
			// Served from the leader's upstream call: log the shared usage,
			// charge nothing, rebase the id onto this request, and persist the
			// rebased copy when the Responses surface asked for storage.
			ctx.Usage = &res.Usage
			ctx.Cost = 0
			ctx.Priced = true
			ctx.Metadata["singleflight_joined"] = true
			hit := rebaseHit(res.Payload, surface)
			if surface == core.SurfaceResponses && responseStoreEnabled(body) {
				var stored map[string]any
				if json.Unmarshal(hit, &stored) == nil {
					stored["_input"] = body["input"]
					if id, ok := stored["id"].(string); ok {
						_ = a.State.Responses.Save(id, keyID(ctx.Auth), stored)
					}
				}
			}
			a.finish(ctx, 200, nil)
			c.Header("x-wiwi-singleflight", "JOINED")
			c.Data(http.StatusOK, "application/json", hit)
			return
		}
		a.finish(ctx, 200, nil)
		c.Data(http.StatusOK, "application/json", res.Payload)
		return
	}

	turn, we := a.State.Gateway.Complete(ctx)
	if we != nil {
		a.finish(ctx, we.Status, we)
		a.writeWiwiErr(c, surface, we)
		return
	}
	resp := wire.EncodeResponse(wire.Surface(surface), *turn, ctx.IRReq.Model, ctx.IRReq)
	if cacheKey != "" {
		if b, err := json.Marshal(resp); err == nil {
			a.State.Cache.Set(cacheKey, b)
		}
	}
	if surface == core.SurfaceResponses && responseStoreEnabled(body) {
		stored := cloneMap(resp)
		stored["_input"] = body["input"]
		if id, ok := resp["id"].(string); ok {
			_ = a.State.Responses.Save(id, keyID(ctx.Auth), stored)
		}
	}
	a.finish(ctx, 200, nil)
	c.JSON(http.StatusOK, resp)
}

func (a *App) cacheable(ctx *core.RequestContext, c *gin.Context) bool {
	if !a.State.Cache.Enabled() || ctx.IRReq.Stream {
		return false
	}
	if a.State.Cfg.Cache.TTLS <= 0 {
		return false
	}
	if bypass := a.State.Cfg.Cache.BypassHeader; bypass != "" {
		v := strings.ToLower(strings.TrimSpace(c.GetHeader(bypass)))
		if v == "1" || v == "true" || v == "yes" {
			return false
		}
	}
	if ctx.IRReq.GenParams.Temperature != nil && *ctx.IRReq.GenParams.Temperature != 0 {
		return false
	}
	if ctx.IRReq.GenParams.N > 1 {
		return false
	}
	return true
}

func cacheKeyFor(surface core.Surface, ctx *core.RequestContext, anthropicBeta string) string {
	// Hash the normalized IR, not the raw body, so two requests that decode to
	// the same canonical request share an entry. The `anthropic-beta` header is
	// part of the key because the Anthropic wire is header-and-body coupled:
	// two requests differing only in that header are two upstream requests and
	// must not share a cached answer (AUDIT #329).
	payload := map[string]any{
		"group":              ctx.Group,
		"surface":            string(surface),
		"key_id":             keyID(ctx.Auth),
		"model":              ctx.IRReq.Model,
		"messages":           ctx.IRReq.Messages,
		"tools":              ctx.IRReq.Tools,
		"tool_choice":        ctx.IRReq.ToolChoice,
		"gen_params":         ctx.IRReq.GenParams,
		"extras":             ctx.IRReq.Extras,
		"mcp_servers":        ctx.IRReq.MCPServers,
		"container":          ctx.IRReq.Container,
		"context_management": ctx.IRReq.ContextManagement,
		"anthropic_beta":     anthropicBeta,
		"stream":             false,
	}
	blob, _ := json.Marshal(payload)
	sum := sha256.Sum256(blob)
	return hex.EncodeToString(sum[:])
}

func (a *App) serveStream(c *gin.Context, ctx *core.RequestContext, surface core.Surface, body map[string]any) {
	streamID := c.GetHeader("x-wiwi-stream-id")
	if streamID == "" {
		streamID = ctx.RequestID
	}
	lastID := int64(0)
	if v := c.GetHeader("Last-Event-ID"); v != "" {
		lastID, _ = strconv.ParseInt(v, 10, 64)
	}

	c.Header("Content-Type", "text/event-stream")
	c.Header("Cache-Control", "no-cache")
	c.Header("Connection", "keep-alive")
	c.Header("x-wiwi-stream-id", streamID)
	if a.State.Cfg.Wiwi.PerfTimings {
		c.Header("x-wiwi-timings", "stream")
	}
	flusher, _ := c.Writer.(http.Flusher)

	enc := wire.NewStreamEncoder(wire.Surface(surface), ctx.IRReq.Model, ctx.RequestID)
	emit := func(payloads []string) {
		for _, p := range payloads {
			a.writeFrame(c, surface, streamID, keyID(ctx.Auth), p, flusher)
		}
	}
	if lastID > 0 {
		for _, rec := range a.State.Journal.Replay(streamID, keyID(ctx.Auth), lastID) {
			if rec.Event != "" {
				c.Writer.Write([]byte("event: " + rec.Event + "\n"))
			}
			c.Writer.Write([]byte("data: " + rec.Data + "\n\n"))
		}
		if flusher != nil {
			flusher.Flush()
		}
	}
	emit(enc.Start())
	var streamErr *providers.WiwiError
	handle := func(d streaming.Delta) {
		if se, ok := d.(streaming.StreamError); ok {
			streamErr = &providers.WiwiError{Status: se.Status, EType: se.EType, Message: se.ErrorText()}
		}
		emit(enc.Encode(d))
	}

	ch := a.State.Gateway.Stream(ctx)
	var ticker *time.Ticker
	var tickC <-chan time.Time
	if ping := a.State.Cfg.Router.StreamPingIntervalS; ping > 0 {
		ticker = time.NewTicker(time.Duration(ping * float64(time.Second)))
		tickC = ticker.C
		defer ticker.Stop()
	}
	done := false
	gone := false
	var graceC <-chan time.Time
	clientGone := c.Request.Context().Done()
	for !done {
		select {
		case d, ok := <-ch:
			if !ok {
				done = true
				break
			}
			if !gone {
				handle(d)
			}
		case <-clientGone:
			// Client disconnected: drain for grace seconds so usage/billing
			// stays accurate, then stop.
			if grace := a.State.Cfg.Router.StreamGraceDrainS; grace > 0 {
				gone = true
				graceC = time.After(time.Duration(grace * float64(time.Second)))
				clientGone = nil
			} else {
				done = true
			}
		case <-graceC:
			done = true
		case <-tickC:
			if surface == core.SurfaceMessages {
				c.Writer.Write([]byte("event: ping\ndata: {\"type\":\"ping\"}\n\n"))
			} else {
				c.Writer.Write([]byte(": ping\n\n"))
			}
			if flusher != nil {
				flusher.Flush()
			}
		}
	}
	emit(enc.End())
	if flusher != nil {
		flusher.Flush()
	}
	status := 200
	if streamErr != nil {
		status = streamErr.Status
	}
	a.finish(ctx, status, streamErr)
}

func (a *App) writeFrame(c *gin.Context, surface core.Surface, streamID, keyID, payload string, flusher http.Flusher) {
	event := ""
	if surface == core.SurfaceMessages {
		var probe struct {
			Type string `json:"type"`
		}
		if json.Unmarshal([]byte(payload), &probe) == nil {
			event = probe.Type
		}
	}
	id := a.State.Journal.Append(streamID, keyID, event, payload)
	if id > 0 && a.State.Cfg.Router.StreamEventIDs {
		c.Writer.Write([]byte("id: " + strconv.FormatInt(id, 10) + "\n"))
	}
	if event != "" {
		c.Writer.Write([]byte("event: " + event + "\n"))
	}
	c.Writer.Write([]byte("data: " + payload + "\n\n"))
	if flusher != nil {
		flusher.Flush()
	}
}

var _ = ir.StopStop
var _ = strings.TrimSpace

// asWiwiError coerces an error from the singleflight callback.
func asWiwiError(err error) *providers.WiwiError {
	if we, ok := err.(*providers.WiwiError); ok {
		return we
	}
	return &providers.WiwiError{Status: 502, EType: "api_error", Message: err.Error(), Retryable: true}
}

func cloneMap(m map[string]any) map[string]any {
	out := make(map[string]any, len(m))
	for k, v := range m {
		out[k] = v
	}
	return out
}

func responseStoreEnabled(body map[string]any) bool {
	v, ok := body["store"]
	if !ok {
		return true
	}
	b, ok := v.(bool)
	return !ok || b
}

// responseHistoryMessages turns a stored Responses turn back into IR: the
// previous INPUT first (user/tool-result items), then the previous OUTPUT
// (assistant text, tool calls). Decoding the raw items preserves tool calls and
// images; a text-only extraction silently dropped them (Python with_history).
func responseHistoryMessages(stored map[string]any, model string) []ir.Message {
	var msgs []ir.Message
	decode := func(items any) []ir.Message {
		if items == nil {
			return nil
		}
		req, err := wire.DecodeRequest(wire.SurfaceResponses, map[string]any{
			"model": model, "input": items,
		})
		if err != nil {
			return nil
		}
		return req.Messages
	}
	msgs = append(msgs, decode(stored["_input"])...)
	msgs = append(msgs, decode(stored["output"])...)
	return msgs
}

// rebaseHit rewrites a cached response's id/created onto the serving request so
// a cache hit is indistinguishable from a fresh response.
func rebaseHit(b []byte, surface core.Surface) []byte {
	var m map[string]any
	if json.Unmarshal(b, &m) != nil {
		return b
	}
	now := time.Now().Unix()
	m["created"] = now
	if surface == core.SurfaceResponses {
		m["created_at"] = now
	}
	prefix := "chatcmpl-"
	switch surface {
	case core.SurfaceMessages:
		prefix = "msg_"
	case core.SurfaceResponses:
		prefix = "resp_"
	case core.SurfaceCompletions:
		prefix = "cmpl-"
	}
	m["id"] = prefix + uuid.NewString()[:24]
	out, err := json.Marshal(m)
	if err != nil {
		return b
	}
	return out
}

func lastUserText(ctx *core.RequestContext) string {
	for i := len(ctx.IRReq.Messages) - 1; i >= 0; i-- {
		m := ctx.IRReq.Messages[i]
		if m.Role != ir.RoleUser {
			continue
		}
		var b strings.Builder
		for _, p := range m.Parts {
			if p.Type == ir.PartText {
				b.WriteString(p.Str("text"))
			}
		}
		return b.String()
	}
	return ""
}
