package streaming

import (
	"reflect"
	"testing"
)

func TestParsePartialComplete(t *testing.T) {
	v, complete := ParsePartial(`{"a":1}`)
	if !complete || !reflect.DeepEqual(v, map[string]any{"a": float64(1)}) {
		t.Fatalf("complete: %v %v", v, complete)
	}
}

func TestRepairTruncated(t *testing.T) {
	cases := []struct {
		in   string
		want map[string]any
	}{
		{`{"a": 1, "b": "x`, map[string]any{"a": float64(1), "b": "x"}},
		{`{"a": tru`, map[string]any{"a": true}},
		{`{"a": 1,`, map[string]any{"a": float64(1)}},
		{`{"n": 1.`, map[string]any{}},
		{`{"qu`, map[string]any{}},
		{`{"a": [1, 2`, map[string]any{"a": []any{float64(1), float64(2)}}},
	}
	for _, c := range cases {
		if got := RepairArgs(c.in); !reflect.DeepEqual(got, c.want) {
			t.Fatalf("repair(%q) = %v, want %v", c.in, got, c.want)
		}
	}
}

func TestParserFeedFinalize(t *testing.T) {
	var p PartialJSONParser
	p.Feed(`{"q": "he`)
	p.Feed(`llo"}`)
	if got := p.Finalize(); got["q"] != "hello" {
		t.Fatalf("finalize: %v", got)
	}
	p.Reset()
	if p.Raw() != "" {
		t.Fatal("reset failed")
	}
}
