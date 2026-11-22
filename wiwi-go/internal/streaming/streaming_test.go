package streaming

import (
	"strings"
	"testing"

	"wiwi-go/internal/ir"
)

func TestSSEParse(t *testing.T) {
	src := "id: 1\nevent: message\ndata: {\"a\":1}\n\ndata: [DONE]\n\n"
	var got []Event
	if err := Parse(strings.NewReader(src), func(e Event) bool { got = append(got, e); return true }); err != nil {
		t.Fatal(err)
	}
	if len(got) != 2 || got[0].Event != "message" || got[0].Data != `{"a":1}` || got[1].Data != "[DONE]" {
		t.Fatalf("bad parse: %+v", got)
	}
}

func TestJournalReplay(t *testing.T) {
	j := NewJournal(true, t.TempDir(), 600, 1<<20)
	if id := j.Append("req1", "k1", "", "one"); id != 1 {
		t.Fatalf("id=%d", id)
	}
	j.Append("req1", "k1", "", "two")
	recs := j.Replay("req1", "k1", 1)
	if len(recs) != 1 || recs[0].Data != "two" {
		t.Fatalf("replay: %+v", recs)
	}
	// wrong key must not see the journal
	if recs := j.Replay("req1", "other", 0); len(recs) != 0 {
		t.Fatalf("key scoping failed: %+v", recs)
	}
}

func TestCoalesce(t *testing.T) {
	c := NewCoalescer(4, 0)
	if out := c.Push(TextDelta{Text: "ab"}); out != nil {
		t.Fatalf("expected buffer, got %v", out)
	}
	out := c.Push(TextDelta{Text: "cdef"})
	td, ok := out.(TextDelta)
	if !ok || td.Text != "abcdef" {
		t.Fatalf("coalesced: %v", out)
	}
}

// TestLoopDetect pins Python's loopdetect.py semantics: with limit=3 the third
// pass over the same chunk is still not evidence (threshold max(3, limit-p)),
// so the stream trips on the fourth. The old Go detector compared only to the
// immediately preceding chunk and reported a trip one chunk early.
func TestLoopDetect(t *testing.T) {
	d := NewLoopDetector(3)
	for i := 1; i <= 3; i++ {
		if d.Observe(TextDelta{Text: "x"}) {
			t.Fatalf("tripped early on chunk %d", i)
		}
	}
	if !d.Observe(TextDelta{Text: "x"}) {
		t.Fatal("expected trip on chunk 4")
	}
}

// TestLoopDetectPeriod covers the shape the old detector could not see at all:
// a repeated sentence (period 2) rather than a repeated single chunk.
func TestLoopDetectPeriod(t *testing.T) {
	d := NewLoopDetector(10)
	pattern := []string{"the same sentence ", "repeated again "}
	tripped := false
	for i := 0; i < 20 && !tripped; i++ {
		tripped = d.Observe(TextDelta{Text: pattern[i%2]})
	}
	if !tripped {
		t.Fatal("period-2 loop was not detected")
	}
}

// TestLoopDetectDisabled: stream_loop_limit <= 0 disables detection entirely.
func TestLoopDetectDisabled(t *testing.T) {
	for _, limit := range []int{0, -1} {
		d := NewLoopDetector(limit)
		for i := 0; i < 500; i++ {
			if d.Observe(TextDelta{Text: "x"}) {
				t.Fatalf("limit=%d tripped", limit)
			}
		}
	}
}

// TestLoopDetectThinking: a model can run away inside its reasoning trace, so
// thinking deltas feed the detector too (Python AUDIT #280).
func TestLoopDetectThinking(t *testing.T) {
	d := NewLoopDetector(4)
	tripped := false
	for i := 0; i < 12 && !tripped; i++ {
		tripped = d.Observe(ThinkingDelta{Text: "hmm "})
	}
	if !tripped {
		t.Fatal("thinking loop was not detected")
	}
}

func TestValidate(t *testing.T) {
	ok := []Delta{
		StreamStart{}, TextDelta{Text: "hi"}, UsageFinal{Prompt: 1, Output: 1},
		Finish{StopReason: ir.StopStop}, StreamEnd{},
	}
	if err := Validate(ok); err != nil {
		t.Fatalf("valid sequence rejected: %v", err)
	}
	bad := []Delta{TextDelta{Text: "hi"}, StreamEnd{}}
	if err := Validate(bad); err == nil {
		t.Fatal("expected invalid sequence")
	}
}

func TestTapeContinuation(t *testing.T) {
	tape := NewStreamTape()
	tape.Observe(StreamStart{})
	tape.Observe(TextDelta{Text: "partial"})
	tape.Observe(ToolCallOpen{Index: 0, ID: "t1", Name: "search"})
	tape.Observe(ToolCallArgsDelta{Index: 0, ArgsFragment: `{"q":`})
	if !tape.HasOutput() || tape.Text() != "partial" {
		t.Fatalf("tape: %q", tape.Text())
	}
	msgs := tape.Continuation()
	if len(msgs) != 2 || msgs[0].Role != ir.RoleAssistant {
		t.Fatalf("continuation: %+v", msgs)
	}
}
