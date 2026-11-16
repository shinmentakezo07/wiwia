package server

import (
	"log"
	"time"
)

// startRetention runs the log retention sweep at startup and on the configured
// cadence. Pruned rows are rolled into request_rollups first, so aggregate
// totals survive the delete.
func (a *App) startRetention() {
	if a.State.Store == nil {
		return
	}
	a.ensureRollups()
	a.pruneLogs()
	interval := a.State.Cfg.Wiwi.LogPruneIntervalS
	if interval <= 0 {
		return
	}
	go func() {
		ticker := time.NewTicker(time.Duration(interval * float64(time.Second)))
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				a.pruneLogs()
				a.State.Journal.Sweep()
			case <-a.State.stop:
				return
			}
		}
	}()
}

func (a *App) ensureRollups() {
	_, _ = a.State.Store.DB().Exec(`CREATE TABLE IF NOT EXISTS request_rollups (
		bucket INTEGER NOT NULL,
		key_id TEXT NOT NULL,
		model_group TEXT NOT NULL,
		provider TEXT NOT NULL,
		requests INTEGER NOT NULL DEFAULT 0,
		prompt_tokens INTEGER NOT NULL DEFAULT 0,
		completion_tokens INTEGER NOT NULL DEFAULT 0,
		cost DOUBLE PRECISION NOT NULL DEFAULT 0,
		PRIMARY KEY (bucket, key_id, model_group, provider)
	)`)
}

// pruneLogs rolls up and deletes rows past the retention window and beyond the
// hard row cap.
func (a *App) pruneLogs() {
	days := a.State.Cfg.Wiwi.LogRetentionDays
	if days > 0 {
		cutoff := time.Now().Unix() - int64(days)*86400
		a.rollupAndDelete(a.State.Store.Rebind(`ts < ?`), cutoff)
	}
	if maxRows := a.State.Cfg.Wiwi.LogMaxRows; maxRows > 0 {
		a.rollupAndDelete(a.State.Store.Rebind(
			`id NOT IN (SELECT id FROM request_logs ORDER BY ts DESC LIMIT ?)`), maxRows)
	}
}

func (a *App) rollupAndDelete(where string, arg any) {
	insert := a.State.Store.Rebind(`INSERT INTO request_rollups
		(bucket,key_id,model_group,provider,requests,prompt_tokens,completion_tokens,cost)
		SELECT ts/3600, key_id, model_group, provider, COUNT(*),
		       COALESCE(SUM(prompt_tokens),0), COALESCE(SUM(completion_tokens),0),
		       COALESCE(SUM(cost),0)
		FROM request_logs WHERE ` + where + `
		GROUP BY ts/3600, key_id, model_group, provider
		ON CONFLICT(bucket,key_id,model_group,provider) DO UPDATE SET
			requests = request_rollups.requests + excluded.requests,
			prompt_tokens = request_rollups.prompt_tokens + excluded.prompt_tokens,
			completion_tokens = request_rollups.completion_tokens + excluded.completion_tokens,
			cost = request_rollups.cost + excluded.cost`)
	if err := a.State.Store.Exec(insert, arg); err != nil {
		log.Printf("wiwi: log rollup failed: %v", err)
		return
	}
	if err := a.State.Store.Exec(a.State.Store.Rebind(`DELETE FROM request_logs WHERE `+where), arg); err != nil {
		log.Printf("wiwi: log prune failed: %v", err)
	}
}
