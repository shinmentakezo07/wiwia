package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"wiwi-go/internal/core"
	"wiwi-go/internal/providers"
	"wiwi-go/internal/wire"
)

func wireError(s core.Surface, status int, etype, msg string) (int, map[string]any) {
	return wire.ErrorBody(wire.Surface(s), status, etype, msg)
}

func (a *App) writeErr(c *gin.Context, s core.Surface, status int, etype, msg string) {
	st, body := wireError(s, status, etype, msg)
	c.JSON(st, body)
}

func (a *App) writeWiwiErr(c *gin.Context, s core.Surface, e *providers.WiwiError) {
	if e == nil {
		a.writeErr(c, s, 500, "api_error", "internal error")
		return
	}
	status := e.Status
	if status == 0 {
		status = 502
	}
	st, body := wire.ErrorBody(wire.Surface(s), status, e.EType, e.Message)
	c.JSON(st, body)
}

func readBody(c *gin.Context) (map[string]any, error) {
	raw, err := io.ReadAll(c.Request.Body)
	if err != nil {
		return nil, err
	}
	if len(strings.TrimSpace(string(raw))) == 0 {
		return map[string]any{}, nil
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		return nil, fmt.Errorf("invalid JSON body: %w", err)
	}
	return body, nil
}

func (a *App) handleHealth(c *gin.Context) {
	// Python's /health returns COUNTS here, not maps. Returning
	// map[group]count / map[provider]type made every value an object, and the
	// Settings page renders ``{health.providers}`` directly, so the console
	// crashed with React #31 ("Objects are not valid as a React child") as
	// soon as a gateway had two providers.
	groups := a.State.Router.Groups()
	providers := a.State.Router.Providers()
	now := time.Now()
	availableGroups := 0
	for _, g := range groups {
		for _, d := range a.State.Router.Deployments(g) {
			if d.Available(now) {
				availableGroups++
				break
			}
		}
	}
	status := "degraded"
	if len(providers) > 0 && availableGroups > 0 {
		status = "ok"
	}
	c.JSON(http.StatusOK, gin.H{
		"status":           status,
		"groups":           len(groups),
		"available_groups": availableGroups,
		"providers":        len(providers),
		"uptime_s":         int(time.Since(a.State.StartTime).Seconds()),
		// Go logs synchronously: no queue drops, no failed asynchronous
		// writes. The fields still exist so dashboards written against the
		// Python backend keep resolving them.
		"dropped_request_logs":      0,
		"failed_request_log_writes": a.State.FailedRequestLogWrites.Load(),
		"dropped_proxy_logs":        0,
		"failed_audit_log_writes":   a.State.Auth.AuditFailures.Load(),
		"dropped_log_events":        0,
		"spend_charge_failures":     a.State.SpendChargeFailures.Load(),
		"spend_flush_failures":      spendFlushFailures(a),
		"realtime_usage_missing":    a.State.RealtimeUsageMissing.Load(),
	})
}

func (a *App) handleModels(c *gin.Context) {
	data := []map[string]any{}
	for _, g := range a.State.Router.Groups() {
		data = append(data, map[string]any{"id": g, "object": "model", "owned_by": "wiwi", "created": 0})
	}
	c.JSON(http.StatusOK, gin.H{"object": "list", "data": data})
}

func (a *App) handlePublicModels(c *gin.Context) {
	c.JSON(http.StatusOK, a.consolePublicModels())
}

func spendFlushFailures(a *App) int64 {
	if a.State.SpendBuffer == nil {
		return 0
	}
	return a.State.SpendBuffer.FlushFailures.Load()
}

func (a *App) handleMetrics(c *gin.Context) {
	// Disabled answers 404 before authorization, exactly as Python does: the
	// endpoint's existence is not an operator secret, and a scrape against a
	// disabled instance should say so rather than 401.
	if !a.State.Cfg.Router.PrometheusEnabled {
		c.String(http.StatusNotFound,
			"Prometheus metrics are disabled. Set "+
				"router_settings.prometheus_enabled: true to enable this endpoint.\n")
		return
	}
	a.requireAdmin(c)
	if c.IsAborted() {
		return
	}
	events := a.State.Bus.EventsSince(0)
	body := renderMetrics(events, a.State.Totals.Snapshot(), metricLossCounters{
		FailedRequestLogWrites: a.State.FailedRequestLogWrites.Load(),
		FailedAuditLogWrites:   a.State.Auth.AuditFailures.Load(),
		SpendChargeFailures:    a.State.SpendChargeFailures.Load(),
		RealtimeUsageMissing:   a.State.RealtimeUsageMissing.Load(),
	})
	c.Header("Content-Type", "text/plain; version=0.0.4")
	c.String(http.StatusOK, body)
}

func (a *App) handleCountTokens(c *gin.Context) {
	body, err := readBody(c)
	if err != nil {
		a.writeErr(c, core.SurfaceMessages, 400, "invalid_request_error", err.Error())
		return
	}
	n := estimatePromptTokens(body)
	c.JSON(http.StatusOK, gin.H{"input_tokens": n})
}

func estimatePromptTokens(body map[string]any) int {
	raw, _ := json.Marshal(body)
	return len(raw)/4 + 1
}

// registerStatic mounts the built SPA with history fallback.
func (a *App) registerStatic() {
	if a.State.StaticDir == "" {
		return
	}
	r := a.Router
	r.NoRoute(func(c *gin.Context) {
		dir := a.State.StaticDir
		if dir == "" {
			c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
			return
		}
		p := c.Request.URL.Path
		if strings.HasPrefix(p, "/v1/") || strings.HasPrefix(p, "/admin/") ||
			strings.HasPrefix(p, "/auth/") || strings.HasPrefix(p, "/public/") ||
			p == "/health" {
			c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
			return
		}
		// Serve a real asset if it exists, else fall back to index.html so the
		// SPA's client-side routes (/login, /app, /docs, …) deep-link. A
		// missing path with a file extension is a genuine 404, not a route.
		full := filepath.Join(dir, filepath.Clean("/"+p))
		if info, err := os.Stat(full); err == nil && !info.IsDir() {
			c.File(full)
			return
		}
		if filepath.Ext(p) != "" {
			c.JSON(http.StatusNotFound, gin.H{"error": "not found"})
			return
		}
		c.File(filepath.Join(dir, "index.html"))
	})
}
