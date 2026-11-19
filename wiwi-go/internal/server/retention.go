package server

import (
	"log"
	"time"

	"wiwi-go/internal/logging"
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

// rollupColumns are the percentile carriers added after the first schema. A
// rollup bucket keeps the full distribution in p95_hist (see logging.Hist*),
// plus the legacy scalar p95 columns for compatibility.
var rollupColumns = []string{
	"tps_sum FLOAT DEFAULT 0",
	"tps_count INTEGER DEFAULT 0",
	"tps_p95 FLOAT DEFAULT 0",
	"ttft_p95_ms FLOAT DEFAULT 0",
	"latency_p95_ms FLOAT DEFAULT 0",
	"errors INTEGER DEFAULT 0",
	"p95_hist TEXT DEFAULT ''",
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
		cost FLOAT NOT NULL DEFAULT 0,
		PRIMARY KEY (bucket, key_id, model_group, provider)
	)`)
	// Columns added after the original table. Postgres supports IF NOT
	// EXISTS; SQLite does not, so the statement is best-effort there and a
	// "duplicate column" error on an existing database is expected.
	for _, col := range rollupColumns {
		_, _ = a.State.Store.DB().Exec(
			`ALTER TABLE request_rollups ADD COLUMN IF NOT EXISTS ` + col)
		_, _ = a.State.Store.DB().Exec(
			`ALTER TABLE request_rollups ADD COLUMN ` + col)
	}
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

type rollupKey struct {
	bucket                 int64
	keyID, group, provider string
}

type rollupAgg struct {
	requests, prompt, completion, errors int64
	cost                                 float64
	tpsSum                               float64
	tpsCount                             int64
	hist                                 map[string]map[int]int
}

// rollupAndDelete aggregates the doomed raw rows into request_rollups and then
// deletes exactly those rows. Percentiles are kept as per-series histograms so
// raw samples and already-rolled-up buckets can be merged by adding bin counts
// (a weighted mean of two p95s is not the p95 of their union).
func (a *App) rollupAndDelete(where string, arg any) {
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT ts,key_id,model_group,provider,COALESCE(status,0),COALESCE(error,''),
		        COALESCE(prompt_tokens,0),COALESCE(completion_tokens,0),
		        COALESCE(cost,0),COALESCE(tps,0),
		        COALESCE(ttft_ms,0),COALESCE(latency_ms,0)
		 FROM request_logs WHERE `+where), arg)
	if err != nil {
		log.Printf("wiwi: log rollup select failed: %v", err)
		return
	}
	groups := map[rollupKey]*rollupAgg{}
	for rows.Next() {
		var ts, prompt, completion, ttft, latency int64
		var keyID, group, provider, errText string
		var status int64
		var cost, tps float64
		if err := rows.Scan(&ts, &keyID, &group, &provider, &status, &errText,
			&prompt, &completion, &cost, &tps, &ttft, &latency); err != nil {
			continue
		}
		k := rollupKey{bucket: (ts / 3600) * 3600, keyID: keyID, group: group, provider: provider}
		agg := groups[k]
		if agg == nil {
			agg = &rollupAgg{hist: map[string]map[int]int{
				"tps": {}, "ttft_ms": {}, "latency_ms": {},
			}}
			groups[k] = agg
		}
		agg.requests++
		if status >= 400 || errText != "" {
			agg.errors++
		}
		agg.prompt += prompt
		agg.completion += completion
		agg.cost += cost
		if tps > 0 {
			agg.tpsSum += tps
			agg.tpsCount++
			logging.AddValue(agg.hist["tps"], tps)
		}
		if ttft > 0 {
			logging.AddValue(agg.hist["ttft_ms"], float64(ttft))
		}
		if latency > 0 {
			logging.AddValue(agg.hist["latency_ms"], float64(latency))
		}
	}
	rows.Close()
	if len(groups) == 0 {
		return
	}

	existing := a.existingRollupHists()
	insert := a.State.Store.Rebind(`INSERT INTO request_rollups
		(bucket,key_id,model_group,provider,requests,prompt_tokens,completion_tokens,
		 cost,errors,tps_sum,tps_count,tps_p95,ttft_p95_ms,latency_p95_ms,p95_hist)
		VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(bucket,key_id,model_group,provider) DO UPDATE SET
			requests = request_rollups.requests + excluded.requests,
			prompt_tokens = request_rollups.prompt_tokens + excluded.prompt_tokens,
			completion_tokens = request_rollups.completion_tokens + excluded.completion_tokens,
			cost = request_rollups.cost + excluded.cost,
			errors = request_rollups.errors + excluded.errors,
			tps_sum = request_rollups.tps_sum + excluded.tps_sum,
			tps_count = request_rollups.tps_count + excluded.tps_count,
			tps_p95 = excluded.tps_p95,
			ttft_p95_ms = excluded.ttft_p95_ms,
			latency_p95_ms = excluded.latency_p95_ms,
			p95_hist = excluded.p95_hist`)
	for k, agg := range groups {
		merged := map[string]map[int]int{}
		prior := existing[k]
		for _, name := range logging.HistSeries {
			merged[name] = logging.MergeHists(prior[name], agg.hist[name])
		}
		if err := a.State.Store.Exec(insert,
			k.bucket, k.keyID, k.group, k.provider, agg.requests, agg.prompt,
			agg.completion, agg.cost, agg.errors, agg.tpsSum, agg.tpsCount,
			logging.HistPercentile(merged["tps"], 0.95),
			logging.HistPercentile(merged["ttft_ms"], 0.95),
			logging.HistPercentile(merged["latency_ms"], 0.95),
			logging.EncodeHists(merged)); err != nil {
			log.Printf("wiwi: log rollup upsert failed: %v", err)
			return
		}
	}
	if err := a.State.Store.Exec(a.State.Store.Rebind(`DELETE FROM request_logs WHERE `+where), arg); err != nil {
		log.Printf("wiwi: log prune failed: %v", err)
	}
}

// existingRollupHists reads every stored histogram so a repeated sweep merges
// into a bucket instead of overwriting what an earlier sweep wrote.
func (a *App) existingRollupHists() map[rollupKey]map[string]map[int]int {
	out := map[rollupKey]map[string]map[int]int{}
	rows, err := a.State.Store.Query(
		`SELECT bucket,key_id,model_group,provider,p95_hist FROM request_rollups WHERE p95_hist != ''`)
	if err != nil {
		return out
	}
	defer rows.Close()
	for rows.Next() {
		var k rollupKey
		var text string
		if rows.Scan(&k.bucket, &k.keyID, &k.group, &k.provider, &text) != nil {
			continue
		}
		out[k] = logging.DecodeHists(text)
	}
	return out
}
