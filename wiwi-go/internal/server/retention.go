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
	a.pruneLogs()
	interval := a.State.Cfg.Wiwi.LogPruneIntervalS
	if interval <= 0 {
		return
	}
	go func() {
		t := time.NewTicker(time.Duration(interval * float64(time.Second)))
		defer t.Stop()
		for {
			select {
			case <-a.State.stop:
				return
			case <-t.C:
				a.pruneLogs()
			}
		}
	}()
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
	bucket                 float64
	keyID, group, provider string
}

type rollupAgg struct {
	requests, tokIn, tokOut, errors int64
	cost                            float64
	tpsSum                          float64
	tpsCount                        int64
	ttftSum                         float64
	latencySum                      float64
	hist                            map[string]map[int]int
}

// rollupAndDelete aggregates the doomed raw rows into request_rollups
// (Python's schema: bucket_ts float, per-bucket token/cost sums plus the
// p95_hist histogram column) and then deletes exactly those rows. Percentiles
// are kept as per-series histograms so raw samples and already-rolled-up
// buckets can be merged by adding bin counts (a weighted mean of two p95s is
// not the p95 of their union).
func (a *App) rollupAndDelete(where string, arg any) {
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT ts,key_id,model_group,provider,COALESCE(status,200),
		        COALESCE(error_message,''),COALESCE(error_code,''),
		        COALESCE(tok_in,0),COALESCE(tok_out,0),
		        COALESCE(cost,0),COALESCE(tps,0),
		        COALESCE(ttft_ms,0),COALESCE(latency_ms,0)
		 FROM request_logs WHERE `+where), arg)
	if err != nil {
		log.Printf("wiwi: log rollup select failed: %v", err)
		return
	}
	groups := map[rollupKey]*rollupAgg{}
	for rows.Next() {
		var ts, tokIn, tokOut float64
		var ttft, latency float64
		var keyID, group, provider, errCode, errMsg string
		var status int64
		var cost, tps float64
		if err := rows.Scan(&ts, &keyID, &group, &provider, &status, &errMsg,
			&errCode, &tokIn, &tokOut, &cost, &tps, &ttft, &latency); err != nil {
			continue
		}
		bucket := float64(int64(ts)/3600) * 3600
		k := rollupKey{bucket: bucket, keyID: keyID, group: group, provider: provider}
		agg := groups[k]
		if agg == nil {
			agg = &rollupAgg{hist: map[string]map[int]int{
				"tps": {}, "ttft_ms": {}, "latency_ms": {},
			}}
			groups[k] = agg
		}
		agg.requests++
		if status >= 400 || errMsg != "" || errCode != "" {
			agg.errors++
		}
		agg.tokIn += int64(tokIn)
		agg.tokOut += int64(tokOut)
		agg.cost += cost
		if tps > 0 {
			agg.tpsSum += tps
			agg.tpsCount++
			logging.AddValue(agg.hist["tps"], tps)
		}
		if ttft > 0 {
			agg.ttftSum += ttft
			logging.AddValue(agg.hist["ttft_ms"], ttft)
		}
		if latency > 0 {
			agg.latencySum += latency
			logging.AddValue(agg.hist["latency_ms"], latency)
		}
	}
	rows.Close()
	if len(groups) == 0 {
		return
	}

	existing := a.existingRollupHists()
	for k, agg := range groups {
		merged := map[string]map[int]int{}
		prior := existing[k]
		for _, name := range logging.HistSeries {
			merged[name] = logging.MergeHists(prior[name], agg.hist[name])
		}
		// Python's schema has no (bucket,key,group,provider) unique key, so
		// merge in two statements: bump the existing bucket if one matches,
		// else insert a fresh row.
		var exists int
		_ = a.State.Store.QueryRow(a.State.Store.Rebind(
			`SELECT 1 FROM request_rollups WHERE bucket_ts=? AND key_id=? AND model_group=? AND provider=?`),
			k.bucket, k.keyID, k.group, k.provider).Scan(&exists)
		if exists == 1 {
			if err := a.State.Store.Exec(a.State.Store.Rebind(
				`UPDATE request_rollups SET requests=requests+?,errors=errors+?,tok_in=tok_in+?,tok_out=tok_out+?,
				 cost=cost+?,tps_sum=tps_sum+?,tps_count=tps_count+?,tps_p95=?,
				 ttft_p95_ms=?,latency_p95_ms=?,p95_hist=? WHERE bucket_ts=? AND key_id=? AND model_group=? AND provider=?`),
				agg.requests, agg.errors, agg.tokIn, agg.tokOut, agg.cost,
				agg.tpsSum, agg.tpsCount,
				logging.HistPercentile(merged["tps"], 0.95),
				logging.HistPercentile(merged["ttft_ms"], 0.95),
				logging.HistPercentile(merged["latency_ms"], 0.95),
				logging.EncodeHists(merged),
				k.bucket, k.keyID, k.group, k.provider); err != nil {
				log.Printf("wiwi: log rollup update failed: %v", err)
				return
			}
			continue
		}
		if err := a.State.Store.Exec(a.State.Store.Rebind(
			`INSERT INTO request_rollups
				(bucket_ts,key_id,model_group,provider,serving_model,requests,errors,tok_in,tok_out,
				 cost,tps_sum,tps_count,tps_p95,ttft_p95_ms,latency_p95_ms,p95_hist)
				VALUES(?,?,?,?,'',?,?,?,?,?,?,?,?,?,?,?)`),
			k.bucket, k.keyID, k.group, k.provider,
			agg.requests, agg.errors, agg.tokIn, agg.tokOut, agg.cost,
			agg.tpsSum, agg.tpsCount,
			logging.HistPercentile(merged["tps"], 0.95),
			logging.HistPercentile(merged["ttft_ms"], 0.95),
			logging.HistPercentile(merged["latency_ms"], 0.95),
			logging.EncodeHists(merged)); err != nil {
			log.Printf("wiwi: log rollup insert failed: %v", err)
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
	rows, err := a.State.Store.Query(a.State.Store.Rebind(
		`SELECT bucket_ts,key_id,model_group,provider,p95_hist FROM request_rollups`))
	if err != nil {
		return nil
	}
	defer rows.Close()
	out := map[rollupKey]map[string]map[int]int{}
	for rows.Next() {
		var k rollupKey
		var hist string
		if rows.Scan(&k.bucket, &k.keyID, &k.group, &k.provider, &hist) != nil {
			continue
		}
		out[k] = logging.DecodeHists(hist)
	}
	return out
}
