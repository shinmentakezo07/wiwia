package logging

// Compact log-scale histograms for percentile rollups.
//
// request_rollups aggregates pruned request_logs rows, so the raw samples
// behind a percentile are gone. Storing one p95 float per bucket cannot be
// merged with raw samples: a weighted mean of two p95s is not the p95 of the
// union. A histogram keeps the distribution at fixed size, so raw samples and
// rolled-up buckets merge by adding bin counts and the percentile is computed
// from the combined counts. Ported from wiwi/logging_core/hist.py.

import (
	"encoding/json"
	"math"
	"sort"
	"strconv"
)

const (
	// BinsPerDecade gives ~10.5% steps (e-fold). Measured p95 error stays
	// under ~4% and under ~1% for the uniform shapes the dashboard shows.
	BinsPerDecade = 10
	// MaxBin is the highest bin index: e^(160/10) = e^16 ≈ 8.9e6.
	MaxBin      = 160
	histVersion = 1
)

var histExp = func() [MaxBin + 1]float64 {
	var out [MaxBin + 1]float64
	for i := range out {
		out[i] = math.Exp(float64(i) / BinsPerDecade)
	}
	return out
}()

// BinIndex returns the bin for value (>= 0). 0 means absent/zero: every
// percentile consumer excludes <= 0 values.
func BinIndex(value float64) int {
	if value <= 0 || math.IsNaN(value) {
		return 0
	}
	if value >= histExp[MaxBin] {
		return MaxBin
	}
	lo, hi := 1, MaxBin
	for lo < hi {
		mid := (lo + hi) / 2
		if histExp[mid] <= value {
			lo = mid + 1
		} else {
			hi = mid
		}
	}
	if lo < 1 {
		lo = 1
	}
	if lo > MaxBin {
		lo = MaxBin
	}
	return lo
}

// BinEdge is the representative value of a bin: its geometric midpoint.
func BinEdge(index int) float64 {
	if index <= 0 {
		return 0
	}
	if index > MaxBin {
		index = MaxBin
	}
	if index == 1 {
		return histExp[1]
	}
	return math.Sqrt(histExp[index-1] * histExp[index])
}

// AddValue accumulates one sample into hist in place.
func AddValue(hist map[int]int, value float64) {
	if idx := BinIndex(value); idx > 0 {
		hist[idx]++
	}
}

// EncodeHist serializes a histogram to the JSON text stored in p95_hist.
func EncodeHist(hist map[int]int) string {
	if len(hist) == 0 {
		return ""
	}
	bins := make(map[string]int, len(hist))
	for k, v := range hist {
		if v > 0 {
			bins[strconv.Itoa(k)] = v
		}
	}
	b, err := json.Marshal(struct {
		V int            `json:"v"`
		B map[string]int `json:"b"`
	}{V: histVersion, B: bins})
	if err != nil {
		return ""
	}
	return string(b)
}

// DecodeHist parses a stored histogram. A blank/legacy/garbled value decodes
// to an empty map so callers fall back to the legacy scalar columns.
func DecodeHist(text string) map[int]int {
	out := map[int]int{}
	if text == "" {
		return out
	}
	var obj struct {
		V int            `json:"v"`
		B map[string]int `json:"b"`
	}
	if err := json.Unmarshal([]byte(text), &obj); err != nil || obj.V != histVersion {
		return out
	}
	for k, cnt := range obj.B {
		idx, err := strconv.Atoi(k)
		if err != nil {
			continue
		}
		if idx > 0 && idx <= MaxBin && cnt > 0 {
			out[idx] += cnt
		}
	}
	return out
}

// MergeHists sums bin counts across histograms.
func MergeHists(hists ...map[int]int) map[int]int {
	out := map[int]int{}
	for _, h := range hists {
		for idx, cnt := range h {
			if cnt > 0 {
				out[idx] += cnt
			}
		}
	}
	return out
}

// PercentileWithSamples computes the nearest-rank p95 over raw samples plus a
// rolled-up histogram. Raw samples keep exact values; rolled-up buckets are
// approximated by their bin midpoints. Mirrors the Python implementation.
func PercentileWithSamples(samples []float64, hist map[int]int) float64 {
	if len(samples) == 0 && len(hist) == 0 {
		return 0
	}
	merged := append([]float64(nil), samples...)
	sort.Float64s(merged)
	if len(hist) > 0 {
		idx := make([]int, 0, len(hist))
		for k := range hist {
			idx = append(idx, k)
		}
		sort.Ints(idx)
		for _, k := range idx {
			edge := BinEdge(k)
			for i := 0; i < hist[k]; i++ {
				merged = append(merged, edge)
			}
		}
		sort.Float64s(merged)
	}
	n := len(merged)
	if n == 0 {
		return 0
	}
	target := int(math.Ceil(float64(n) * 0.95))
	if target < 1 {
		target = 1
	}
	if target > n {
		target = n
	}
	return merged[target-1]
}

// HistSeries are the distributions a rollup bucket carries.
var HistSeries = []string{"tps", "ttft_ms", "latency_ms"}

// EncodeHists stores every series' histogram as one p95_hist value:
// {"tps": "<hist json>", "ttft_ms": "...", "latency_ms": "..."}.
func EncodeHists(series map[string]map[int]int) string {
	out := map[string]string{}
	for name, h := range series {
		if enc := EncodeHist(h); enc != "" {
			out[name] = enc
		}
	}
	if len(out) == 0 {
		return ""
	}
	b, err := json.Marshal(out)
	if err != nil {
		return ""
	}
	return string(b)
}

// DecodeHists parses a stored p95_hist into {series: {bin: count}}. Tolerant:
// a blank/legacy/garbled value decodes to an empty map.
func DecodeHists(text string) map[string]map[int]int {
	out := map[string]map[int]int{}
	if text == "" {
		return out
	}
	var raw map[string]string
	if err := json.Unmarshal([]byte(text), &raw); err != nil {
		return out
	}
	for name, enc := range raw {
		if h := DecodeHist(enc); len(h) > 0 {
			out[name] = h
		}
	}
	return out
}

// HistPercentile is the nearest-rank percentile over a histogram alone.
func HistPercentile(hist map[int]int, p float64) float64 {
	total := 0
	for _, c := range hist {
		if c > 0 {
			total += c
		}
	}
	if total <= 0 {
		return 0
	}
	target := 1
	if p > 0 {
		target = int(math.Ceil(float64(total) * p))
		if target < 1 {
			target = 1
		}
		if target > total {
			target = total
		}
	}
	idx := make([]int, 0, len(hist))
	for k := range hist {
		if hist[k] > 0 {
			idx = append(idx, k)
		}
	}
	sort.Ints(idx)
	seen := 0
	for _, k := range idx {
		seen += hist[k]
		if seen >= target {
			return BinEdge(k)
		}
	}
	return BinEdge(idx[len(idx)-1])
}
