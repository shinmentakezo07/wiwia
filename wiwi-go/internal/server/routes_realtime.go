package server

import (
	"encoding/json"
	"net/http"
	"net/url"
	"strconv"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"

	"wiwi-go/internal/core"
	"wiwi-go/internal/ir"
	"wiwi-go/internal/providers"
	"wiwi-go/internal/router"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// requireRealtimeAuth is requireAPI plus the documented ?key= fallback. A
// browser WebSocket constructor cannot set an Authorization header, so Python
// admits the key in the query string when realtime.allow_key_in_query is on.
// The route used to sit inside the requireAPI group, which made the configured
// fallback unreachable.
func (a *App) requireRealtimeAuth(c *gin.Context) {
	if bearer(c) == "" && a.State.Cfg.Realtime.AllowKeyInQuery {
		if k := c.Query("key"); k != "" {
			c.Request.Header.Set("Authorization", "Bearer "+k)
		}
	}
	a.requireAPI(c)
}

// handleRealtime admits and bills a Realtime WebSocket session like any other
// request, then relays frames in both directions. Admission happens before the
// HTTP upgrade so refusals are real 4xx/5xx responses, not an accepted socket
// that immediately closes.
func (a *App) handleRealtime(c *gin.Context) {
	if !a.State.Cfg.Realtime.Enabled {
		a.writeErr(c, core.SurfaceChat, 404, "invalid_request_error",
			"realtime is disabled; set realtime.enabled: true to enable it")
		return
	}
	ai := authOf(c)
	if ai == nil {
		a.writeErr(c, core.SurfaceChat, 401, "authentication_error", "invalid or missing API key")
		return
	}
	model := c.Query("model")
	if model == "" {
		a.writeErr(c, core.SurfaceChat, 400, "invalid_request_error",
			"model query parameter required")
		return
	}
	if len(ai.Models) > 0 && !modelAllowed(ai.Models, model) {
		a.writeErr(c, core.SurfaceChat, 403, "permission_error", "model not allowed for this key")
		return
	}
	group := a.State.Router.ResolveGroup(model)
	if !a.State.Router.HasGroup(group) {
		a.writeErr(c, core.SurfaceChat, 404, "not_found_error", "model not found: "+model)
		return
	}

	// Per-key and gateway rate limits, exactly as the HTTP path admits them.
	bucket := ai.KeyID
	if ai.IsMaster {
		bucket = "master"
	}
	if ok, retry := a.State.Limiter.Allow("key:"+bucket, ai.RPM, ai.TPM, 0); !ok {
		c.Header("Retry-After", strconv.Itoa(int(retry)+1))
		a.writeErr(c, core.SurfaceChat, 429, "rate_limit_error", "rate limit exceeded")
		return
	}
	if ok, retry := a.State.Limiter.Allow("global", a.State.Router.GlobalRPM(), a.State.Router.GlobalTPM(), 0); !ok {
		c.Header("Retry-After", strconv.Itoa(int(retry)+1))
		a.writeErr(c, core.SurfaceChat, 429, "rate_limit_error", "gateway rate limit exceeded")
		return
	}

	// Unpriced-model policy fails closed before a socket exists.
	if a.State.Cfg.General.UnpricedModelPolicy == "reject" {
		priced := false
		for _, d := range a.State.Router.Deployments(group) {
			if _, ok := a.State.Cost.Compute(d.Model, ir.Usage{}); ok {
				priced = true
				break
			}
		}
		if !priced {
			a.writeErr(c, core.SurfaceChat, 503, "service_unavailable",
				"model is not priced; ask an admin to add a price")
			return
		}
	}

	sessionID := c.Query("session_id")
	if sessionID == "" {
		sessionID = c.GetHeader("x-wiwi-session-id")
	}
	lane := c.GetHeader("x-wiwi-lane")

	deps := a.State.Router.Deployments(group)
	exclude := map[string]bool{}
	var dep *router.Deployment
	var key *router.ProviderKey
	for len(exclude) < len(deps) {
		d, err := a.State.Router.PickDeployment(group, exclude)
		if err != nil {
			break
		}
		if !providers.RealtimeSupported(d.Provider.Type) {
			exclude[d.Name()] = true
			continue
		}
		k, err := d.PickKey(nil)
		if err != nil {
			exclude[d.Name()] = true
			continue
		}
		if !a.State.Router.ReserveSlot(d, lane) {
			exclude[d.Name()] = true
			continue
		}
		dep, key = d, k
		break
	}
	if dep == nil {
		// Distinguish "no capacity" from "nothing in this group speaks
		// realtime": both would otherwise be a misleading 503.
		unsupported := true
		for _, d := range deps {
			if providers.RealtimeSupported(d.Provider.Type) {
				unsupported = false
				break
			}
		}
		if unsupported {
			a.writeErr(c, core.SurfaceChat, 501, "not_implemented_error",
				"provider has no realtime surface")
		} else {
			a.writeErr(c, core.SurfaceChat, 503, "service_unavailable",
				"no healthy deployment with a key for "+group)
		}
		return
	}
	defer a.State.Router.ReleaseSlot(dep, lane)

	adapter, err := providers.FreshAdapter(dep.Provider.Type)
	if err != nil {
		a.writeErr(c, core.SurfaceChat, 501, "not_implemented_error",
			"provider has no realtime surface")
		return
	}
	upURL, ok := providers.RealtimeURL(dep.Provider.Type, dep.Provider.BaseURL)
	if !ok {
		a.writeErr(c, core.SurfaceChat, 501, "not_implemented_error",
			"provider has no realtime surface")
		return
	}
	upURL += "?model=" + url.QueryEscape(dep.Model)

	started := time.Now()
	ctx := &core.RequestContext{
		Surface: core.SurfaceChat, IRReq: ir.Request{Model: model},
		Started: started, RequestID: requestID(c), Auth: ai, Group: group,
		Deployment: dep, SessionID: sessionID, Lane: lane,
		Metadata: map[string]any{},
	}
	// Conservative reservation, settled against the real usage at session end
	// (a zero-usage session gets the reservation refunded).
	if !ai.IsMaster && ai.MaxBudget > 0 {
		if err := a.State.Auth.ReserveBudget(ai.KeyID, 0.01); err != nil {
			a.writeErr(c, core.SurfaceChat, 402, "budget_exceeded", "budget exceeded")
			return
		}
		ctx.BudgetReserved = 0.01
	}

	client, err := upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		// The handshake failed before a session existed; refund and stay silent
		// (the HTTP response already went out).
		if ctx.BudgetReserved > 0 {
			_ = a.State.Auth.Settle(ai.KeyID, ctx.BudgetReserved, 0)
		}
		return
	}
	defer client.Close()

	headers := http.Header{}
	for k, v := range adapter.Headers(providers.ProviderKeyRef{Label: key.Label, Secret: key.Secret}) {
		headers.Set(k, v)
	}
	if headers.Get("OpenAI-Beta") == "" {
		headers.Set("OpenAI-Beta", "realtime=v1")
	}
	upstream, _, err := websocket.DefaultDialer.Dial(upURL, headers)
	if err != nil {
		_ = client.WriteMessage(websocket.TextMessage,
			[]byte(`{"type":"error","error":{"message":"upstream realtime unavailable"}}`))
		we := &providers.WiwiError{Status: 502, EType: "api_error",
			Message: "upstream realtime unavailable"}
		a.finishRealtime(ctx, dep, key, 502, we, 0, 0, started)
		return
	}
	defer upstream.Close()

	var peakMu sync.Mutex
	promptPeak, completionPeak := 0, 0
	observe := func(data []byte) {
		in, out, ok := parseRealtimeUsage(data)
		if !ok {
			return
		}
		peakMu.Lock()
		if in+out > promptPeak+completionPeak {
			promptPeak, completionPeak = in, out
		}
		peakMu.Unlock()
	}

	errc := make(chan error, 2)
	upstreamDone := make(chan struct{})
	var wg sync.WaitGroup
	pump := func(dst, src *websocket.Conn, onText func([]byte)) {
		defer wg.Done()
		for {
			mt, data, err := src.ReadMessage()
			if err != nil {
				errc <- err
				return
			}
			if mt == websocket.TextMessage && onText != nil {
				onText(data)
			}
			if err := dst.WriteMessage(mt, data); err != nil {
				errc <- err
				return
			}
		}
	}
	wg.Add(2)
	go func() {
		defer close(upstreamDone)
		pump(client, upstream, observe)
	}()
	go pump(upstream, client, nil)

	idle := time.Duration(a.State.Cfg.Realtime.IdleTimeoutS * float64(time.Second))
	maxSession := time.Duration(a.State.Cfg.Realtime.MaxSessionS * float64(time.Second))
	resetDeadlines := func() {
		if idle > 0 {
			_ = client.SetReadDeadline(time.Now().Add(idle))
			_ = upstream.SetReadDeadline(time.Now().Add(idle))
		}
	}
	resetDeadlines()
	stopKeepalive := make(chan struct{})
	if idle > 0 {
		go func() {
			ticker := time.NewTicker(idle / 2)
			defer ticker.Stop()
			for {
				select {
				case <-ticker.C:
					resetDeadlines()
				case <-stopKeepalive:
					return
				}
			}
		}()
	}

	select {
	case <-errc:
	case <-c.Request.Context().Done():
	case <-time.After(maxSession):
		// Give the provider a short grace to flush its final
		// response.done/usage frame before the socket is torn down; the peak
		// usage would otherwise be lost on every max-session cutoff.
		select {
		case <-upstreamDone:
		case <-time.After(750 * time.Millisecond):
		}
	}
	close(stopKeepalive)
	_ = client.Close()
	_ = upstream.Close()
	wg.Wait()

	peakMu.Lock()
	in, out := promptPeak, completionPeak
	peakMu.Unlock()
	a.finishRealtime(ctx, dep, key, 200, nil, in, out, started)
}

// finishRealtime prices the usage peak, settles the budget reservation, and
// records the session exactly like an HTTP request (log row, totals, metrics,
// request event).
func (a *App) finishRealtime(ctx *core.RequestContext, dep *router.Deployment,
	key *router.ProviderKey, status int, we *providers.WiwiError, prompt, completion int,
	started time.Time) {
	usage := ir.Usage{PromptTokens: prompt, CompletionTokens: completion}
	if prompt+completion == 0 {
		// Served but not priced: count it, because a zero-usage realtime
		// session is invisible in the cost series otherwise (AUDIT #347).
		usage.Estimated = true
		a.State.RealtimeUsageMissing.Add(1)
	}
	ctx.Usage = &usage
	if pricedCost, ok := a.State.Cost.Compute(dep.Model, usage); ok {
		ctx.Cost = pricedCost
		ctx.Priced = true
	}
	latency := int(time.Since(started).Milliseconds())
	ctx.NoteAttempt(dep.Name(), dep.ProviderName(), key.Label,
		strconv.Itoa(status), latency, "", dep.Model)
	a.finish(ctx, status, we)
}

// parseRealtimeUsage extracts the token counts carried by a realtime event.
// Providers put them either at the top level (usage) or inside the final
// response object (response.usage).
func parseRealtimeUsage(data []byte) (int, int, bool) {
	type usage struct {
		InputTokens  int `json:"input_tokens"`
		OutputTokens int `json:"output_tokens"`
	}
	var env struct {
		Usage    *usage `json:"usage"`
		Response *struct {
			Usage *usage `json:"usage"`
		} `json:"response"`
	}
	if json.Unmarshal(data, &env) != nil {
		return 0, 0, false
	}
	u := env.Usage
	if u == nil && env.Response != nil {
		u = env.Response.Usage
	}
	if u == nil || (u.InputTokens == 0 && u.OutputTokens == 0) {
		return 0, 0, false
	}
	return u.InputTokens, u.OutputTokens, true
}
