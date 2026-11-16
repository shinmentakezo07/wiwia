package streaming

// LoopDetector terminates degenerate repetition. It counts consecutive
// identical text chunks; when the count reaches the limit the stream is cut
// with a clean Finish (not an error) so the client sees a normal end.
type LoopDetector struct {
	limit   int
	last    string
	repeats int
}

func NewLoopDetector(limit int) *LoopDetector { return &LoopDetector{limit: limit} }

// Observe records a delta and reports whether the stream should be aborted.
func (l *LoopDetector) Observe(d Delta) bool {
	if l == nil || l.limit <= 0 {
		return false
	}
	td, ok := d.(TextDelta)
	if !ok {
		if _, isText := d.(TextDelta); !isText {
			l.last = ""
			l.repeats = 0
		}
		return false
	}
	if td.Text == l.last && td.Text != "" {
		l.repeats++
	} else {
		l.last = td.Text
		l.repeats = 1
	}
	return l.repeats >= l.limit
}

// Reset clears detector state (used when resuming onto a new upstream).
func (l *LoopDetector) Reset() {
	if l != nil {
		l.last = ""
		l.repeats = 0
	}
}
