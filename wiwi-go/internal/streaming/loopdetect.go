package streaming

import "fmt"

// LoopDetector is a faithful port of wiwi/streaming/loopdetect.py: it aborts a
// stream once the same content repeats with a short period.
//
// The old Go version only compared each chunk to the immediately preceding one,
// so it caught "the same token forever" and nothing else — not the commonest
// runaway shape, the model restating the same sentence or paragraph, which is
// 10-40 chunks on the wire (Python AUDIT #280).
type LoopDetector struct {
	limit  int
	period int
	recent []string
	runs   []int
}

// maxLoopPeriod is the longest repetition period detected. It covers both
// shapes runaway decoding produces — one chunk repeated, and a sentence or
// paragraph restated — at 32 integer comparisons per chunk.
const maxLoopPeriod = 32

// minLoopRepeats is the fewest repeats that count as evidence of a loop. A
// single confirming repetition is coincidence, not a runaway decode: two chunks
// matching 30 apart is ordinary prose (Python AUDIT #481).
const minLoopRepeats = 3

// NewLoopDetector builds a detector; limit <= 0 disables detection.
func NewLoopDetector(limit int) *LoopDetector {
	if limit < 0 {
		limit = 0
	}
	l := &LoopDetector{limit: limit}
	if limit > 0 {
		l.period = maxLoopPeriod
		l.runs = make([]int, maxLoopPeriod+1)
	}
	return l
}

// threshold is the repeat count that makes a window of `limit` chunks exactly
// periodic, floored at minLoopRepeats so a small limit never turns a
// coincidence into an abort.
func (l *LoopDetector) threshold(period int) int {
	if t := l.limit - period; t > minLoopRepeats {
		return t
	}
	return minLoopRepeats
}

// Feed records one generated chunk and reports whether the stream is looping.
// It is chunk-type agnostic by design: a model can run away inside its
// reasoning trace, so thinking deltas are fed as well as visible text. An empty
// chunk is ignored — it carries no evidence, and counting provider keep-alive
// frames as repetitions would trip the detector.
func (l *LoopDetector) Feed(text string) bool {
	if l == nil || l.period == 0 || text == "" {
		return false
	}
	n := len(l.recent)
	top := n
	if top > l.period {
		top = l.period
	}
	looping := false
	for p := 1; p <= top; p++ {
		if text == l.recent[n-p] {
			l.runs[p]++
		} else {
			l.runs[p] = 0
		}
		if l.runs[p] >= l.threshold(p) {
			looping = true
		}
	}
	l.recent = append(l.recent, text)
	if len(l.recent) > l.period {
		l.recent = l.recent[len(l.recent)-l.period:]
	}
	return looping
}

// Observe adapts Feed to the delta stream. Every content-bearing delta is fed
// (text and thinking), matching Python; other delta kinds are ignored without
// resetting the window, because a tool-call frame between two repeats is not
// evidence that the repetition stopped.
func (l *LoopDetector) Observe(d Delta) bool {
	switch v := d.(type) {
	case TextDelta:
		return l.Feed(v.Text)
	case ThinkingDelta:
		return l.Feed(v.Text)
	}
	return false
}

// Reset clears detector state (used when resuming onto a new upstream).
func (l *LoopDetector) Reset() {
	if l == nil {
		return
	}
	l.recent = nil
	if l.runs != nil {
		for i := range l.runs {
			l.runs[i] = 0
		}
	}
}

func (l *LoopDetector) String() string {
	if l == nil {
		return "LoopDetector(nil)"
	}
	return fmt.Sprintf("LoopDetector(limit=%d)", l.limit)
}
