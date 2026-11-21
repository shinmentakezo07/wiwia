package server

import (
	"fmt"
	"math"
	"sort"
	"strings"
	"time"

	"wiwi-go/internal/logging"
)

// Prometheus exposition header. Kept byte-identical to the Python exporter's
// _HEADER (wiwi/server/metrics.py) so dashboards and alerts written against the
// Python backend keep resolving the same series names and types.
const metricsHeader = `# HELP wiwi_requests_total Total number of requests.
# TYPE wiwi_requests_total counter
# HELP wiwi_tokens_total Token usage by kind (input/output/cached/cache_creation/reasoning).
# TYPE wiwi_tokens_total counter
# HELP wiwi_prompt_cache_hits_total Requests served with a provider prompt-cache hit.
# TYPE wiwi_prompt_cache_hits_total counter
# HELP wiwi_prompt_cache_hit_rate Fraction of requests in the current window with a prompt-cache hit.
# TYPE wiwi_prompt_cache_hit_rate gauge
# HELP wiwi_response_cache_hits_total Requests served from wiwi's exact-match response cache.
# TYPE wiwi_response_cache_hits_total counter
# HELP wiwi_usage_estimated_requests_total Requests whose token counts were estimated locally because upstream omitted usage.
# TYPE wiwi_usage_estimated_requests_total counter
# HELP wiwi_cost_total Total cost in USD.
# TYPE wiwi_cost_total counter
# HELP wiwi_stream_errors_total Mid-stream failures.
# TYPE wiwi_stream_errors_total counter
# HELP wiwi_requests_by_status Requests by HTTP status code in the current window.
# TYPE wiwi_requests_by_status gauge
# HELP wiwi_requests_by_provider Requests by serving provider account in the current window.
# TYPE wiwi_requests_by_provider gauge
# HELP wiwi_request_logs_dropped_total Request-log events dropped because the logging queue was full.
# TYPE wiwi_request_logs_dropped_total counter
# HELP wiwi_request_logs_failed_total Request-log rows lost to a failed database write.
# TYPE wiwi_request_logs_failed_total counter
# HELP wiwi_proxy_logs_dropped_total Proxy-log events dropped because the logging queue was full.
# TYPE wiwi_proxy_logs_dropped_total counter
# HELP wiwi_audit_logs_failed_total Audit rows lost to a failed database write.
# TYPE wiwi_audit_logs_failed_total counter
# HELP wiwi_log_events_dropped_total Log events lost on any stream, by any loss mode.
# TYPE wiwi_log_events_dropped_total counter
# HELP wiwi_spend_charge_failures_total Spend charges that failed to persist (the budget cap was not enforced).
# TYPE wiwi_spend_charge_failures_total counter
# HELP wiwi_realtime_usage_missing_total Realtime sessions that ended without a usage event (served but not priced).
# TYPE wiwi_realtime_usage_missing_total counter
# HELP wiwi_request_duration_ms Request latency in milliseconds (scrape-time quantiles).
# TYPE wiwi_request_duration_ms summary
# HELP wiwi_ttft_ms Time to first token in milliseconds (scrape-time quantiles).
# TYPE wiwi_ttft_ms summary
# HELP wiwi_tps Output generation speed in tokens per second (scrape-time quantiles; streaming requests only).
# TYPE wiwi_tps summary
# HELP wiwi_tps_sample_ratio Share of window requests that carry a measurable TPS sample.
# TYPE wiwi_tps_sample_ratio gauge
`

// renderMetrics renders the exposition body from the request-event ring
// window (summary/breakdown values) plus process-lifetime totals and loss
// counters. The split is deliberate: quantiles are honestly per-scrape, while
// counters must never decrease on ring eviction (AUDIT #180).
func renderMetrics(events []logging.Event, totals map[string]float64,
	spendChargeFailures, realtimeUsageMissing int64) string {
	var b strings.Builder
	b.WriteString(metricsHeader)
	// The Go backend logs synchronously, so it has no queue drops or failed
	// asynchronous writes; the four loss-series are still exported at zero so
	// a dashboard that references them does not break after a backend switch.
	b.WriteString("wiwi_request_logs_dropped_total 0\n")
	b.WriteString("wiwi_request_logs_failed_total 0\n")
	b.WriteString("wiwi_proxy_logs_dropped_total 0\n")
	b.WriteString("wiwi_audit_logs_failed_total 0\n")
	b.WriteString("wiwi_log_events_dropped_total 0\n")
	fmt.Fprintf(&b, "wiwi_spend_charge_failures_total %d\n", spendChargeFailures)
	fmt.Fprintf(&b, "wiwi_realtime_usage_missing_total %d\n", realtimeUsageMissing)

	reqs := totals["requests"]
	fmt.Fprintf(&b, "wiwi_requests_total %s\n", floatText(reqs))
	fmt.Fprintf(&b, "wiwi_tokens_total{kind=\"input\"} %s\n", floatText(totals["prompt_tokens"]))
	fmt.Fprintf(&b, "wiwi_tokens_total{kind=\"output\"} %s\n", floatText(totals["completion_tokens"]))
	fmt.Fprintf(&b, "wiwi_tokens_total{kind=\"cached\"} %s\n", floatText(totals["cached_tokens"]))
	fmt.Fprintf(&b, "wiwi_tokens_total{kind=\"cache_creation\"} %s\n", floatText(totals["cache_creation_tokens"]))
	fmt.Fprintf(&b, "wiwi_tokens_total{kind=\"reasoning\"} %s\n", floatText(totals["reasoning_tokens"]))
	fmt.Fprintf(&b, "wiwi_prompt_cache_hits_total %s\n", floatText(totals["prompt_cache_hits"]))
	fmt.Fprintf(&b, "wiwi_response_cache_hits_total %s\n", floatText(totals["response_cache_hits"]))
	fmt.Fprintf(&b, "wiwi_usage_estimated_requests_total %s\n", floatText(totals["usage_estimated"]))
	fmt.Fprintf(&b, "wiwi_cost_total %.6f\n", totals["cost"])
	fmt.Fprintf(&b, "wiwi_stream_errors_total %s\n", floatText(totals["stream_errors"]))

	if len(events) == 0 {
		b.WriteString("wiwi_prompt_cache_hit_rate 0\n")
		b.WriteString("wiwi_tps_sample_ratio 0\n")
		fmt.Fprintf(&b, "# ts %f\n", timeNowSeconds())
		return b.String()
	}

	var durations, ttfts, tpsValues []float64
	statusCounts := map[int]int{}
	providerCounts := map[string]int{}
	windowHits, requestEvents := 0, 0
	for _, e := range events {
		if e.Type != "request" || e.Data == nil {
			continue
		}
		requestEvents++
		if v := eventNum(e.Data, "latency_ms"); v > 0 {
			durations = append(durations, v)
		}
		if v := eventNum(e.Data, "ttft_ms"); v > 0 {
			ttfts = append(ttfts, v)
		}
		if v := eventNum(e.Data, "tps"); v > 0 {
			tpsValues = append(tpsValues, v)
		}
		statusCounts[int(eventNum(e.Data, "status"))]++
		if p := eventStr(e.Data, "provider"); p != "" {
			providerCounts[p]++
		}
		if eventBool(e.Data, "cache_hit") || eventNum(e.Data, "tok_cached") > 0 {
			windowHits++
		}
	}

	total := requestEvents
	rate := 0.0
	if total > 0 {
		rate = roundTo(float64(windowHits)/float64(total), 4)
	}
	fmt.Fprintf(&b, "wiwi_prompt_cache_hit_rate %s\n", floatText(rate))

	statuses := make([]int, 0, len(statusCounts))
	for s := range statusCounts {
		statuses = append(statuses, s)
	}
	sort.Ints(statuses)
	for _, s := range statuses {
		fmt.Fprintf(&b, "wiwi_requests_by_status{status=\"%d\"} %d\n", s, statusCounts[s])
	}
	providers := make([]string, 0, len(providerCounts))
	for p := range providerCounts {
		providers = append(providers, p)
	}
	sort.Strings(providers)
	for _, p := range providers {
		fmt.Fprintf(&b, "wiwi_requests_by_provider{provider=\"%s\"} %d\n",
			escapeLabel(p), providerCounts[p])
	}

	writeQuantiles(&b, "wiwi_request_duration_ms", durations, 1)
	writeQuantiles(&b, "wiwi_ttft_ms", ttfts, 1)
	writeQuantiles(&b, "wiwi_tps", tpsValues, 2)
	ratio := 0.0
	if total > 0 {
		ratio = roundTo(float64(len(tpsValues))/float64(total), 4)
	}
	fmt.Fprintf(&b, "wiwi_tps_sample_ratio %s\n", floatText(ratio))
	fmt.Fprintf(&b, "# ts %f\n", timeNowSeconds())
	return b.String()
}

func timeNowSeconds() float64 {
	return float64(time.Now().UnixNano()) / 1e9
}

func writeQuantiles(b *strings.Builder, name string, values []float64, digits int) {
	if len(values) == 0 {
		return
	}
	sort.Float64s(values)
	for _, q := range []float64{0.5, 0.95, 0.99} {
		fmt.Fprintf(b, "%s{quantile=\"%s\"} %.*f\n",
			name, strconvQuantile(q), digits, percentileSorted(values, q))
	}
}

func strconvQuantile(q float64) string {
	if q == 0.5 {
		return "0.5"
	}
	if q == 0.95 {
		return "0.95"
	}
	return "0.99"
}

// percentileSorted is the nearest-rank percentile of an already-sorted slice
// (0 when empty), matching wiwi/server/stats.py:percentile_sorted.
func percentileSorted(vals []float64, p float64) float64 {
	if len(vals) == 0 {
		return 0
	}
	i := int(math.Ceil(float64(len(vals))*p)) - 1
	if i < 0 {
		i = 0
	}
	if i >= len(vals) {
		i = len(vals) - 1
	}
	return vals[i]
}

func eventNum(d map[string]any, key string) float64 {
	switch v := d[key].(type) {
	case int:
		return float64(v)
	case int64:
		return float64(v)
	case float64:
		return v
	case float32:
		return float64(v)
	}
	return 0
}

func eventBool(d map[string]any, key string) bool {
	v, _ := d[key].(bool)
	return v
}

func eventStr(d map[string]any, key string) string {
	v, _ := d[key].(string)
	return v
}

func floatText(v float64) string {
	return strings.TrimSuffix(strings.TrimRight(fmt.Sprintf("%.6f", v), "0"), ".")
}

func escapeLabel(v string) string {
	v = strings.ReplaceAll(v, `\`, `\\`)
	v = strings.ReplaceAll(v, `"`, `\"`)
	return strings.ReplaceAll(v, "\n", `\n`)
}
