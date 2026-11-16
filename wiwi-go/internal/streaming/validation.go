package streaming

import "fmt"

// Machine validates the delta ordering contract. Adapters are expected to
// produce legal sequences; the machine exists for assertions and tests.
type Machine struct {
	started  bool
	finished bool
	ended    bool
	open     map[int]bool
	usage    int
}

func NewMachine() *Machine { return &Machine{open: map[int]bool{}} }

// Push validates one delta and advances state.
func (m *Machine) Push(d Delta) error {
	if m.ended {
		return fmt.Errorf("delta after terminal")
	}
	switch v := d.(type) {
	case StreamStart:
		if m.started {
			return fmt.Errorf("duplicate StreamStart")
		}
		m.started = true
	case TextDelta, ThinkingDelta, LogprobsDelta, ServerToolResultDelta:
		if !m.started {
			return fmt.Errorf("content before StreamStart")
		}
	case ToolCallOpen:
		if m.open[v.Index] {
			return fmt.Errorf("tool index %d opened twice", v.Index)
		}
		m.open[v.Index] = true
	case ToolCallArgsDelta:
		if !m.open[v.Index] {
			return fmt.Errorf("args for closed index %d", v.Index)
		}
	case ToolCallClose:
		if !m.open[v.Index] {
			return fmt.Errorf("close for unopened index %d", v.Index)
		}
		delete(m.open, v.Index)
	case UsageFinal:
		m.usage++
	case Finish:
		if m.finished {
			return fmt.Errorf("duplicate Finish")
		}
		m.finished = true
	case StreamEnd:
		if !m.finished {
			return fmt.Errorf("StreamEnd before Finish")
		}
		m.ended = true
	case StreamError:
		m.ended = true
	}
	return nil
}

// Validate checks a complete delta slice.
func Validate(deltas []Delta) error {
	m := NewMachine()
	for _, d := range deltas {
		if err := m.Push(d); err != nil {
			return err
		}
	}
	if !m.ended {
		return fmt.Errorf("stream did not terminate")
	}
	return nil
}
