package streaming

import "time"

// Coalescer merges consecutive TextDeltas under backpressure, bounded by a byte
// cap and a time window. It is optional (stream_coalesce) and changes no
// semantics — only chunk granularity.
type Coalescer struct {
	maxBytes int
	maxAge   time.Duration
	buf      []byte
	last     time.Time
	active   bool
}

func NewCoalescer(maxBytes int, maxAge time.Duration) *Coalescer {
	if maxBytes <= 0 {
		maxBytes = 8192
	}
	if maxAge <= 0 {
		maxAge = 50 * time.Millisecond
	}
	return &Coalescer{maxBytes: maxBytes, maxAge: maxAge}
}

// Push returns a TextDelta to emit now (merged or original), or nil when the
// delta was buffered for a later flush.
func (c *Coalescer) Push(d Delta) Delta {
	td, ok := d.(TextDelta)
	if !ok {
		return d
	}
	now := time.Now()
	if c.active && (len(c.buf) >= c.maxBytes || now.Sub(c.last) >= c.maxAge) {
		out := TextDelta{Text: string(c.buf)}
		c.buf = nil
		c.active = false
		// start a new buffer with this delta
		c.buf = append(c.buf, td.Text...)
		c.last = now
		c.active = true
		return out
	}
	c.buf = append(c.buf, td.Text...)
	c.last = now
	c.active = true
	if len(c.buf) >= c.maxBytes {
		out := TextDelta{Text: string(c.buf)}
		c.buf = nil
		c.active = false
		return out
	}
	return nil
}

// Flush drains any buffered text.
func (c *Coalescer) Flush() Delta {
	if !c.active || len(c.buf) == 0 {
		return nil
	}
	out := TextDelta{Text: string(c.buf)}
	c.buf = nil
	c.active = false
	return out
}
