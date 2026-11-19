package logging

import (
	"math"
	"testing"
)

func TestHistEncodeDecodeRoundTrip(t *testing.T) {
	h := map[int]int{}
	for _, v := range []float64{10, 10, 100, 1000} {
		AddValue(h, v)
	}
	text := EncodeHist(h)
	if text == "" {
		t.Fatal("expected encoded histogram")
	}
	back := DecodeHist(text)
	if len(back) != len(h) {
		t.Fatalf("bins differ: %v vs %v", h, back)
	}
	total := 0
	for _, c := range back {
		total += c
	}
	if total != 4 {
		t.Fatalf("expected 4 samples, got %d", total)
	}
}

func TestDecodeHistTolerant(t *testing.T) {
	for _, text := range []string{"", "garbage", `{"v":2,"b":{"1":1}}`, `{"v":1,"b":[]}`} {
		if h := DecodeHist(text); len(h) != 0 {
			t.Fatalf("expected empty for %q, got %v", text, h)
		}
	}
}

func TestBinIndexIgnoresNonPositive(t *testing.T) {
	for _, v := range []float64{0, -1, math.NaN()} {
		if got := BinIndex(v); got != 0 {
			t.Fatalf("BinIndex(%v) = %d, want 0", v, got)
		}
	}
	if BinIndex(1e12) != MaxBin {
		t.Fatal("values past the range must clamp to MaxBin")
	}
}

func TestPercentileWithSamplesUnionsDistribution(t *testing.T) {
	// One raw fast sample plus one rolled-up slow sample: the union's p95 is
	// the slow one, not the fast raw value.
	hist := map[int]int{}
	AddValue(hist, 1000)
	got := PercentileWithSamples([]float64{10}, hist)
	if got < 800 || got > 1200 {
		t.Fatalf("p95 = %v, want ~1000", got)
	}
}

func TestHistPercentileNearestRank(t *testing.T) {
	h := map[int]int{}
	for _, v := range []float64{10, 20, 30, 40, 50} {
		AddValue(h, v)
	}
	got := HistPercentile(h, 0.95)
	if got < 40 || got > 55 {
		t.Fatalf("p95 = %v, want the top of the distribution", got)
	}
}

func TestEncodeHistsRoundTrip(t *testing.T) {
	series := map[string]map[int]int{"tps": {}, "ttft_ms": {}, "latency_ms": {}}
	AddValue(series["tps"], 42)
	AddValue(series["latency_ms"], 900)
	text := EncodeHists(series)
	back := DecodeHists(text)
	if len(back["tps"]) == 0 || len(back["latency_ms"]) == 0 {
		t.Fatalf("series lost: %v", back)
	}
	if _, ok := back["ttft_ms"]; ok {
		t.Fatalf("empty series should not be encoded: %v", back)
	}
}
