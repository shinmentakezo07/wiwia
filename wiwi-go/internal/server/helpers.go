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
	groups := map[string]int{}
	for _, g := range a.State.Router.Groups() {
		groups[g] = len(a.State.Router.Deployments(g))
	}
	providersMap := map[string]string{}
	for name, acct := range a.State.Router.Providers() {
		providersMap[name] = acct.Type
	}
	c.JSON(http.StatusOK, gin.H{
		"status":    "ok",
		"groups":    groups,
		"providers": providersMap,
		"uptime_s":  int(time.Since(a.State.StartTime).Seconds()),
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

func (a *App) handleMetrics(c *gin.Context) {
	snap := a.State.Totals.Snapshot()
	a.State.Metrics.Set("wiwi_requests_total", snap["requests"])
	a.State.Metrics.Set("wiwi_errors_total", snap["errors"])
	a.State.Metrics.Set("wiwi_prompt_tokens_total", snap["prompt_tokens"])
	a.State.Metrics.Set("wiwi_completion_tokens_total", snap["completion_tokens"])
	a.State.Metrics.Set("wiwi_cost_usd_total", snap["cost"])
	c.Header("Content-Type", "text/plain; version=0.0.4")
	c.String(http.StatusOK, a.State.Metrics.Render())
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
