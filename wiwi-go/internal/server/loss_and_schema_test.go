package server

import (
	"encoding/json"
	"strings"
	"testing"

	"wiwi-go/internal/config"
	"wiwi-go/internal/core"
	"wiwi-go/internal/ir"
)

func TestLossCountersReachHealthAndMetrics(t *testing.T) {
	app, _ := newCountingApp(t, func(c *config.Config) {
		c.Router.PrometheusEnabled = true
	})
	ctx := &core.RequestContext{
		Surface: core.SurfaceChat, RequestID: "lost-row",
		Auth:     &core.AuthInfo{KeyID: "master", IsMaster: true},
		Metadata: map[string]any{}, Usage: &ir.Usage{PromptTokens: 1},
	}
	// Close the database so both write paths fail the way a broken DB does.
	if err := app.State.Store.Close(); err != nil {
		t.Fatal(err)
	}
	app.logRequest(ctx)
	app.State.Auth.Audit("master", "test", "target", "")

	if got := app.State.FailedRequestLogWrites.Load(); got != 1 {
		t.Fatalf("failed request-log writes = %d", got)
	}
	if got := app.State.Auth.AuditFailures.Load(); got != 1 {
		t.Fatalf("failed audit writes = %d", got)
	}
	health := do(t, app, "GET", "/health", "").Body.String()
	for _, want := range []string{
		`"failed_request_log_writes":1`,
		`"failed_audit_log_writes":1`,
	} {
		if !strings.Contains(health, want) {
			t.Fatalf("health missing %s: %s", want, health)
		}
	}
	metrics := do(t, app, "GET", "/metrics", "").Body.String()
	for _, want := range []string{
		"wiwi_request_logs_failed_total 1",
		"wiwi_audit_logs_failed_total 1",
	} {
		if !strings.Contains(metrics, want) {
			t.Fatalf("metrics missing %q:\n%s", want, metrics)
		}
	}
}

func TestResponseStoreWritesPythonSchema(t *testing.T) {
	app, _ := newCountingApp(t, nil)
	rs := app.State.Responses
	body := map[string]any{
		"id": "resp_1", "object": "response", "output": []any{},
		"_input": []any{map[string]any{"role": "user", "content": "hi"}},
	}
	if err := rs.Save("resp_1", "key-a", "gpt-test", body); err != nil {
		t.Fatal(err)
	}
	var keyID, surface, group, inputJSON, outputJSON string
	if err := app.State.Store.QueryRow(app.State.Store.Rebind(
		`SELECT key_id,surface,model_group,input_json,output_json
		 FROM stored_responses WHERE id=?`), "resp_1").
		Scan(&keyID, &surface, &group, &inputJSON, &outputJSON); err != nil {
		t.Fatal(err)
	}
	if keyID != "key-a" || surface != "responses" || group != "gpt-test" {
		t.Fatalf("scope columns: key=%q surface=%q group=%q", keyID, surface, group)
	}
	if strings.Contains(outputJSON, "_input") {
		t.Fatalf("output_json leaked the transport-only _input: %s", outputJSON)
	}
	var input []map[string]any
	if err := json.Unmarshal([]byte(inputJSON), &input); err != nil || len(input) != 1 {
		t.Fatalf("input_json: %v %s", err, inputJSON)
	}
	got, ok := rs.Get("resp_1", "key-a")
	if !ok || got["id"] != "resp_1" {
		t.Fatalf("get: ok=%v body=%v", ok, got)
	}
	if _, ok := got["_input"]; !ok {
		t.Fatal("Get did not reattach _input for history replay")
	}
	// The legacy Go-only table must no longer be part of the shared schema.
	var count int
	if err := app.State.Store.QueryRow(
		`SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='responses'`).Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatal("legacy responses table is still created")
	}
}
