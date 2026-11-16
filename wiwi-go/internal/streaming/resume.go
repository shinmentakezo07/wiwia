package streaming

import (
	"fmt"
	"strings"

	"wiwi-go/internal/ir"
)

// StreamTape records the output a stream has produced so far so a mid-stream
// failure can fail over to a fallback deployment with the partial output
// prepended. It is the resume substrate (stream_resume).
type StreamTape struct {
	text     strings.Builder
	thinking strings.Builder
	tools    map[int]*tapeTool
	order    []int
	usage    UsageFinal
	finished bool
	stop     ir.StopReason
	seq      int
}

type tapeTool struct {
	ID   string
	Name string
	Args strings.Builder
}

func NewStreamTape() *StreamTape {
	return &StreamTape{tools: map[int]*tapeTool{}}
}

func (t *StreamTape) Observe(d Delta) {
	t.seq++
	switch v := d.(type) {
	case TextDelta:
		t.text.WriteString(v.Text)
	case ThinkingDelta:
		t.thinking.WriteString(v.Text)
	case ToolCallOpen:
		if t.tools[v.Index] == nil {
			t.tools[v.Index] = &tapeTool{ID: v.ID, Name: v.Name}
			t.order = append(t.order, v.Index)
		} else {
			t.tools[v.Index].ID = v.ID
			t.tools[v.Index].Name = v.Name
		}
	case ToolCallArgsDelta:
		if tt := t.tools[v.Index]; tt != nil {
			tt.Args.WriteString(v.ArgsFragment)
		}
	case UsageFinal:
		t.usage = v
	case Finish:
		t.finished = true
		t.stop = v.StopReason
	}
}

func (t *StreamTape) Text() string      { return t.text.String() }
func (t *StreamTape) Thinking() string  { return t.thinking.String() }
func (t *StreamTape) HasOutput() bool   { return t.text.Len() > 0 || len(t.tools) > 0 }
func (t *StreamTape) Usage() UsageFinal { return t.usage }
func (t *StreamTape) Seq() int          { return t.seq }

// ToolCalls reconstructs the partial tool calls in open order.
func (t *StreamTape) ToolCalls() []ir.ToolUsePart {
	var out []ir.ToolUsePart
	for _, idx := range t.order {
		tt := t.tools[idx]
		if tt == nil {
			continue
		}
		out = append(out, ir.ToolUsePart{
			ID: tt.ID, Name: tt.Name, RawArgs: tt.Args.String(), BlockType: "tool_use",
		})
	}
	return out
}

// Continuation returns the messages to prepend to a fallback request so the
// model continues from the partial output instead of restarting.
func (t *StreamTape) Continuation() []ir.Message {
	if !t.HasOutput() {
		return nil
	}
	partial := t.Text()
	var parts []ir.Part
	if partial != "" {
		parts = append(parts, ir.NewTextPart(partial))
	}
	msgs := []ir.Message{{Role: ir.RoleAssistant, Parts: parts}}
	if len(t.tools) > 0 {
		var names []string
		for _, idx := range t.order {
			names = append(names, t.tools[idx].Name)
		}
		msgs = append(msgs, ir.Message{Role: ir.RoleUser, Parts: []ir.Part{
			ir.NewTextPart(fmt.Sprintf("Continue from where you left off. Do not repeat the text above. Pending tool calls: %s", strings.Join(names, ", "))),
		}})
	} else {
		msgs = append(msgs, ir.Message{Role: ir.RoleUser, Parts: []ir.Part{
			ir.NewTextPart("Continue from where you left off. Do not repeat the text above."),
		}})
	}
	return msgs
}
