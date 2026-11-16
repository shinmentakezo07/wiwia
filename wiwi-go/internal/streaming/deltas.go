package streaming

import "wiwi-go/internal/ir"

// Delta is the single tagged union the streaming pump emits and every dialect
// encoder consumes. Adapters guarantee ordering; encoders never defend.
//
// Ordering contract:
//
//	StreamStart (exactly one, first)
//	TextDelta* / ThinkingDelta* /
//	ToolCallOpen -> ToolCallArgsDelta* -> ToolCallClose (per index; siblings allowed)
//	ServerToolResultDelta / LogprobsDelta
//	UsageFinal (at least once; may repeat; may precede last content delta)
//	Finish
//	StreamEnd xor StreamError
//
// StreamError may terminate at ANY point and needs no preceding Finish.
//
// NOTE: the canonical tool-args delta type is named ToolCallArgsDelta (matches the
// Python IRStreamDelta taxonomy). ArgsDelta is kept as a deprecated alias.
type Delta interface{ isDelta() }

type StreamStart struct {
	Model         string
	Group         string
	Prompt        int
	Cached        int
	CacheCreation int
}

func (StreamStart) isDelta() {}

type TextDelta struct{ Text string }

func (TextDelta) isDelta() {}

type ThinkingDelta struct {
	Text             string
	Signature        string
	BlockType        string
	Data             string
	EncryptedContent string
}

func (ThinkingDelta) isDelta() {}

type ToolCallOpen struct {
	Index     int
	ID        string
	Name      string
	Builtin   string
	BlockType string
}

func (ToolCallOpen) isDelta() {}

type ToolCallArgsDelta struct {
	Index        int
	ArgsFragment string
}

func (ToolCallArgsDelta) isDelta() {}

// ArgsDelta is a convenience alias for ToolCallArgsDelta used by some adapters.
type ArgsDelta = ToolCallArgsDelta

type ToolCallClose struct{ Index int }

func (ToolCallClose) isDelta() {}

type ServerToolResultDelta struct {
	Index   int
	Block   map[string]any
	Builtin string
}

func (ServerToolResultDelta) isDelta() {}

type LogprobsDelta struct{ Logprobs map[string]any }

func (LogprobsDelta) isDelta() {}

type UsageFinal struct {
	Prompt         int
	Cached         int
	Reasoning      int
	Output         int
	CacheCreation  int
	Estimated      bool
	Cost           float64
	CachedInPrompt *bool
}

func (UsageFinal) isDelta() {}

type Finish struct {
	StopReason   ir.StopReason
	StopSequence string
}

// Finish also exposes a string Reason accessor for encoders that want the wire value.
func (f Finish) Reason() string {
	r := string(f.StopReason)
	if r == "" {
		r = string(ir.StopStop)
	}
	return r
}

func (Finish) isDelta() {}

type StreamEnd struct{}

func (StreamEnd) isDelta() {}

type StreamError struct {
	Message string
	Kind    string // "timeout" | "connection" | "status" | "cancelled" | "unknown"
	Status  int
	EType   string
	Err     error
}

func (StreamError) isDelta() {}

func (e StreamError) ErrorText() string {
	if e.Message != "" {
		return e.Message
	}
	if e.Err != nil {
		return e.Err.Error()
	}
	return "stream error"
}

// StreamErrorType is wiwi's classification of a mid-stream failure, for any
// dialect to render. Prefers the vendor's own type when named.
func StreamErrorType(d StreamError) string {
	if d.EType != "" {
		return d.EType
	}
	switch d.Status {
	case 529:
		return "overloaded_error"
	case 429:
		return "rate_limit_error"
	}
	if d.Status == 504 || d.Kind == "timeout" {
		return "timeout"
	}
	if d.Status == 408 || d.Status == 500 || d.Status == 502 || d.Status == 503 {
		return "api_connection_error"
	}
	return "api_error"
}
