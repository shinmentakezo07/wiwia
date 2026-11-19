package core

import (
	"encoding/json"
	"strings"

	"wiwi-go/internal/ir"
	"wiwi-go/internal/streaming"
)

// streamAssembler turns the canonical delta stream back into one AssistantTurn.
// It exists for force_stream providers: their upstream has no non-streaming
// mode, so a plain request is served by pumping SSE and reassembling it.
type streamAssembler struct {
	text      strings.Builder
	thinking  []ir.ThinkingPart
	tools     map[int]*ir.ToolUsePart
	toolOrder []int
	usage     ir.Usage
	stop      ir.StopReason
	stopSeq   *string
	server    []map[string]any
	logprobs  map[string]any
	content   bool
	finished  bool
}

func newStreamAssembler() *streamAssembler {
	return &streamAssembler{tools: map[int]*ir.ToolUsePart{}}
}

func (a *streamAssembler) observe(d streaming.Delta) {
	switch v := d.(type) {
	case streaming.StreamStart:
		a.content = true
	case streaming.TextDelta:
		a.text.WriteString(v.Text)
		a.content = true
	case streaming.ThinkingDelta:
		a.content = true
		if v.Text != "" || v.Signature != "" || v.Data != "" || v.EncryptedContent != "" {
			if len(a.thinking) == 0 {
				a.thinking = append(a.thinking, ir.ThinkingPart{BlockType: v.BlockType})
			}
			last := &a.thinking[len(a.thinking)-1]
			if v.BlockType != "" && last.BlockType != "" && v.BlockType != last.BlockType {
				a.thinking = append(a.thinking, ir.ThinkingPart{BlockType: v.BlockType})
				last = &a.thinking[len(a.thinking)-1]
			}
			if v.BlockType != "" {
				last.BlockType = v.BlockType
			}
			last.Text += v.Text
			if v.Signature != "" {
				last.Signature = v.Signature
			}
			if v.Data != "" {
				last.Data = v.Data
			}
			if v.EncryptedContent != "" {
				last.EncryptedContent = v.EncryptedContent
			}
		}
	case streaming.ToolCallOpen:
		a.content = true
		t := a.tools[v.Index]
		if t == nil {
			t = &ir.ToolUsePart{BlockType: v.BlockType, Builtin: v.Builtin}
			if t.BlockType == "" {
				t.BlockType = "tool_use"
			}
			a.tools[v.Index] = t
			a.toolOrder = append(a.toolOrder, v.Index)
		}
		if v.ID != "" {
			t.ID = v.ID
		}
		if v.Name != "" {
			t.Name = v.Name
		}
		if v.Builtin != "" {
			t.Builtin = v.Builtin
		}
		if v.BlockType != "" {
			t.BlockType = v.BlockType
		}
	case streaming.ToolCallArgsDelta:
		a.content = true
		t := a.tools[v.Index]
		if t == nil {
			t = &ir.ToolUsePart{BlockType: "tool_use"}
			a.tools[v.Index] = t
			a.toolOrder = append(a.toolOrder, v.Index)
		}
		t.RawArgs += v.ArgsFragment
	case streaming.ToolCallClose:
		a.finishTool(v.Index)
	case streaming.ServerToolResultDelta:
		a.content = true
		if v.Block != nil {
			a.server = append(a.server, v.Block)
		}
	case streaming.LogprobsDelta:
		if v.Logprobs != nil {
			a.logprobs = v.Logprobs
		}
	case streaming.UsageFinal:
		a.usage = ir.Usage{
			PromptTokens: v.Prompt, CompletionTokens: v.Output,
			CachedTokens: v.Cached, ReasoningTokens: v.Reasoning,
			CacheCreationTokens: v.CacheCreation, Estimated: v.Estimated,
			CachedInPrompt: v.CachedInPrompt,
		}
	case streaming.Finish:
		a.finished = true
		if v.StopReason != "" {
			a.stop = v.StopReason
		}
		if v.StopSequence != "" {
			seq := v.StopSequence
			a.stopSeq = &seq
		}
	}
}

func (a *streamAssembler) finishTool(index int) {
	t := a.tools[index]
	if t == nil {
		return
	}
	if t.RawArgs == "" {
		if t.Args == nil {
			t.Args = map[string]any{}
		}
		return
	}
	args := map[string]any{}
	if err := json.Unmarshal([]byte(t.RawArgs), &args); err != nil {
		args = map[string]any{"_raw": t.RawArgs}
	}
	t.Args = args
}

func (a *streamAssembler) turn() *ir.AssistantTurn {
	tools := make([]ir.ToolUsePart, 0, len(a.toolOrder))
	for _, idx := range a.toolOrder {
		t := a.tools[idx]
		if t == nil {
			continue
		}
		if t.RawArgs != "" {
			a.finishTool(idx)
		} else if t.Args == nil {
			t.Args = map[string]any{}
		}
		tools = append(tools, *t)
	}
	stop := a.stop
	if stop == "" {
		if len(tools) > 0 {
			stop = ir.StopToolCall
		} else {
			stop = ir.StopStop
		}
	}
	return &ir.AssistantTurn{
		Text: a.text.String(), Thinking: a.thinking, ToolCalls: tools,
		StopReason: stop, StopSequence: a.stopSeq, Usage: a.usage,
		ServerBlocks: a.server, Logprobs: a.logprobs,
	}
}
