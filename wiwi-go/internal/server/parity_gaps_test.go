package server

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"
)

func insertLog(t *testing.T, app *App, id string, ts int64, tps, ttft, latency float64) {
	t.Helper()
	_, err := app.State.Store.DB().Exec(app.State.Store.Rebind(
		`INSERT INTO request_logs(id,request_id,ts,key_id,model_group,provider,model_id,status,
		 prompt_tokens,completion_tokens,cached_tokens,reasoning_tokens,cost,latency_ms,ttft_ms,tps,error,attempts)
		 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`),
		id, id, ts, "k", "g", "p", "m", 200, 10, 5, 0, 0, 0.1,
		int(latency), int(ttft), tps, "", "[]")
	if err != nil {
		t.Fatalf("insert %s: %v", id, err)
	}
}

func TestAdminLogFiltersAndPagination(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	now := time.Now().Unix()
	for i := 0; i < 5; i++ {
		insertLog(t, app, fmt.Sprintf("row%d", i), now-int64(i), 0, 0, 0)
	}
	insertLog(t, app, "old", now-7200, 0, 0, 0)

	var body struct {
		Logs []map[string]any `json:"logs"`
	}
	w := do(t, app, "GET", "/admin/logs/requests?limit=2&offset=1", "")
	if w.Code != 200 {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Logs) != 2 {
		t.Fatalf("limit/offset returned %d rows", len(body.Logs))
	}
	if w.Header().Get("Cache-Control") != "no-store" {
		t.Fatal("logs must be no-store")
	}

	// minutes=1 excludes the two-hour-old row.
	w = do(t, app, "GET", "/admin/logs/requests?minutes=1", "")
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	for _, row := range body.Logs {
		if row["request_id"] == "old" {
			t.Fatal("minutes window included a stale row")
		}
	}

	// Audit pagination and clamping.
	for i := 0; i < 3; i++ {
		_, _ = app.State.Store.DB().Exec(app.State.Store.Rebind(
			`INSERT INTO audit_logs(id,ts,actor,action,target,diff) VALUES(?,?,?,?,?,?)`),
			fmt.Sprintf("a%d", i), now-int64(i), "admin", "x", "t", "{}")
	}
	w = do(t, app, "GET", "/admin/logs/audit?limit=2", "")
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Logs) != 2 {
		t.Fatalf("audit limit returned %d rows", len(body.Logs))
	}

	// Proxy tail keeps the newest 500 entries.
	for i := 0; i < 505; i++ {
		app.State.Bus.Publish("proxy", map[string]any{"level": "info", "message": "m"})
	}
	w = do(t, app, "GET", "/admin/logs/proxy", "")
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Logs) != 500 {
		t.Fatalf("proxy tail returned %d rows, want 500", len(body.Logs))
	}
}

func TestRollupHistogramsFeedStatsOverview(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	// Old rows so pruneLogs rolls them up.
	insertLog(t, app, "old1", 1, 10, 10, 10)
	insertLog(t, app, "old2", 2, 100, 100, 100)
	insertLog(t, app, "old3", 3, 1000, 1000, 1000)
	app.pruneLogs()

	var hist string
	if err := app.State.Store.QueryRow(
		`SELECT p95_hist FROM request_rollups LIMIT 1`).Scan(&hist); err != nil {
		t.Fatalf("rollup row missing: %v", err)
	}
	if hist == "" {
		t.Fatal("rollup did not store a percentile histogram")
	}

	w := do(t, app, "GET", "/admin/stats/overview?minutes=0", "")
	if w.Code != 200 {
		t.Fatalf("overview %d: %s", w.Code, w.Body.String())
	}
	var stats map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &stats); err != nil {
		t.Fatal(err)
	}
	if stats["requests"].(float64) != 3 {
		t.Fatalf("requests = %v, want 3 (rolled-up rows must still count)", stats["requests"])
	}
	if stats["latency_p95_ms"].(float64) <= 0 {
		t.Fatalf("latency p95 = %v, want the rolled-up histogram", stats["latency_p95_ms"])
	}
	if stats["tps_avg"].(float64) <= 0 || stats["tps_p95"].(float64) <= 0 {
		t.Fatalf("tps stats missing: avg=%v p95=%v", stats["tps_avg"], stats["tps_p95"])
	}
	if stats["ttft_p95_ms"].(float64) <= 0 {
		t.Fatalf("ttft p95 = %v", stats["ttft_p95_ms"])
	}
}
