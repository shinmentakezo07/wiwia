package server

import (
	"encoding/json"
	"strings"

	"wiwi-go/internal/core"
)

// evaluateAlerts checks the operator-defined alert rules (stored as JSON in the
// settings table) against the running totals and publishes an event when a
// threshold is crossed. Rules are a list of {"metric": "...", "threshold": N}.
func (a *App) evaluateAlerts(ctx *core.RequestContext) {
	raw, ok, err := a.State.Store.GetSetting("alert_rules")
	if err != nil || !ok || strings.TrimSpace(raw) == "" {
		return
	}
	var rules []struct {
		Metric    string  `json:"metric"`
		Threshold float64 `json:"threshold"`
	}
	if json.Unmarshal([]byte(raw), &rules) != nil {
		return
	}
	snap := a.State.Totals.Snapshot()
	for _, r := range rules {
		v, exists := snap[r.Metric]
		if !exists {
			continue
		}
		if v > r.Threshold {
			a.State.Bus.Publish("alert", map[string]any{
				"metric": r.Metric, "value": v, "threshold": r.Threshold,
				"request_id": ctx.RequestID,
			})
			a.State.Metrics.Inc("wiwi_alerts_fired_total", 1)
		}
	}
}
