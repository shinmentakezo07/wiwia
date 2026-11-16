package providers

import (
	"encoding/json"
	"fmt"
	"strconv"
	"strings"

	"wiwi-go/internal/streaming"
)

// Normalize native MiniMax-M3 / StepFun tool markup leaked by NVIDIA NIM.
// Some NIM-hosted models emit tool calls as text using the ]<]minimax[>[ tag
// namespace instead of proper tool_calls deltas; this converts that markup into
// ordinary tool-call deltas.

const (
	nimNamespace    = "]<]minimax[>["
	nimToolStart    = nimNamespace + "<tool_call>"
	nimToolEnd      = nimNamespace + "</tool_call>"
	nimInvokeStart  = nimNamespace + "<invoke"
	nimInvokeEnd    = nimNamespace + "</invoke>"
	nimElemStart    = nimNamespace + "<"
	nimElemEnd      = nimNamespace + "</"
	nimMaxToolChars = 4 * 1024 * 1024
	nimMaxDepth     = 64
)

type miniMaxMode int

const (
	nimText miniMaxMode = iota
	nimToolBlock
	nimAfterBlock
	nimFinished
)

// miniMaxFramer separates visible text from one native tool block.
type miniMaxFramer struct {
	mode      miniMaxMode
	textTail  string
	toolTail  string
	toolParts []string
	toolLen   int
	toolBlock string
}

func partialMarkerSuffix(s, marker string) int {
	max := len(marker) - 1
	if max > len(s) {
		max = len(s)
	}
	for n := max; n > 0; n-- {
		if strings.HasSuffix(s, marker[:n]) {
			return n
		}
	}
	return 0
}

// Feed returns (visibleText, completedBlock, done).
func (f *miniMaxFramer) Feed(text string) (string, string, bool) {
	if f.mode == nimFinished || text == "" {
		return "", "", false
	}
	if f.mode == nimToolBlock {
		return "", f.feedToolBlock(text), false
	}
	if f.mode == nimAfterBlock {
		return "", "", false
	}
	candidate := f.textTail + text
	idx := strings.Index(candidate, nimToolStart)
	held := partialMarkerSuffix(candidate, nimToolStart)
	if idx >= 0 {
		visible := candidate[:idx]
		f.textTail = ""
		f.mode = nimToolBlock
		block := f.feedToolBlock(candidate[idx+len(nimToolStart):])
		return visible, block, block != ""
	}
	if held > 0 {
		f.textTail = candidate[len(candidate)-held:]
		return candidate[:len(candidate)-held], "", false
	}
	f.textTail = ""
	return candidate, "", false
}

func (f *miniMaxFramer) feedToolBlock(text string) string {
	candidate := f.toolTail + text
	if end := strings.Index(candidate, nimToolEnd); end >= 0 {
		f.appendTool(candidate[:end])
		block := strings.Join(f.toolParts, "")
		f.toolParts = nil
		f.toolTail = ""
		f.mode = nimAfterBlock
		f.toolBlock = block
		return block
	}
	held := partialMarkerSuffix(candidate, nimToolEnd)
	if held > 0 {
		f.appendTool(candidate[:len(candidate)-held])
		f.toolTail = candidate[len(candidate)-held:]
	} else {
		f.appendTool(candidate)
		f.toolTail = ""
	}
	return ""
}

func (f *miniMaxFramer) appendTool(fragment string) {
	if fragment == "" {
		return
	}
	f.toolLen += len(fragment)
	if f.toolLen > nimMaxToolChars {
		return
	}
	f.toolParts = append(f.toolParts, fragment)
}

// Finish flushes a held-back text tail (dropping an incomplete marker).
func (f *miniMaxFramer) Finish() string {
	if f.mode == nimFinished {
		return ""
	}
	if f.mode == nimText {
		tail := f.textTail
		f.textTail = ""
		f.mode = nimFinished
		if strings.Contains(tail, nimNamespace) {
			return ""
		}
		return tail
	}
	f.toolParts = nil
	f.toolTail = ""
	f.mode = nimFinished
	return ""
}

// -- parser -----------------------------------------------------------------

type nimElement struct {
	name     string
	text     string
	children []nimElement
}

type nimCall struct {
	name string
	args map[string]any
}

func parseNimToolBlock(block string, schemas map[string]map[string]any, aliases map[string]map[string]string) ([]nimCall, error) {
	cursor := 0
	var calls []nimCall
	for {
		for cursor < len(block) && isSpace(block[cursor]) {
			cursor++
		}
		if cursor >= len(block) {
			break
		}
		name, body, next, err := parseNimInvoke(block, cursor)
		if err != nil {
			return nil, err
		}
		cursor = next
		args, err := parseNimArguments(body, schemas[name])
		if err != nil {
			return nil, err
		}
		if am := aliases[name]; len(am) > 0 {
			args = reverseNIMArgs(name, args, aliases)
		}
		calls = append(calls, nimCall{name: name, args: args})
	}
	if len(calls) == 0 {
		return nil, fmt.Errorf("empty native tool block")
	}
	return calls, nil
}

func parseNimInvoke(text string, cursor int) (string, string, int, error) {
	if !strings.HasPrefix(text[cursor:], nimInvokeStart) {
		return "", "", 0, fmt.Errorf("malformed invoke tag")
	}
	tagEnd := strings.IndexByte(text[cursor+len(nimInvokeStart):], '>')
	if tagEnd < 0 {
		return "", "", 0, fmt.Errorf("incomplete invoke tag")
	}
	tagEnd += cursor + len(nimInvokeStart)
	attrs := text[cursor+len(nimInvokeStart) : tagEnd]
	name, err := parseNimInvokeName(attrs)
	if err != nil {
		return "", "", 0, err
	}
	bodyStart := tagEnd + 1
	bodyEnd := strings.Index(text[bodyStart:], nimInvokeEnd)
	if bodyEnd < 0 {
		return "", "", 0, fmt.Errorf("incomplete invoke block")
	}
	bodyEnd += bodyStart
	return name, text[bodyStart:bodyEnd], bodyEnd + len(nimInvokeEnd), nil
}

func parseNimInvokeName(attrs string) (string, error) {
	v := strings.TrimSpace(attrs)
	if !strings.HasPrefix(v, "name") {
		return "", fmt.Errorf("invoke without name attribute")
	}
	rest := strings.TrimLeft(v[len("name"):], " \t\r\n")
	if !strings.HasPrefix(rest, "=") {
		return "", fmt.Errorf("malformed name attribute")
	}
	rest = strings.TrimSpace(rest[1:])
	if rest == "" {
		return "", fmt.Errorf("empty tool name")
	}
	q := rest[0]
	var name string
	if q == '"' || q == '\'' {
		if len(rest) < 2 || rest[len(rest)-1] != q {
			return "", fmt.Errorf("unterminated tool name")
		}
		name = rest[1 : len(rest)-1]
	} else {
		name = rest
	}
	name = strings.TrimSpace(name)
	if name == "" {
		return "", fmt.Errorf("empty tool name")
	}
	return name, nil
}

func parseNimArguments(body string, schema map[string]any) (map[string]any, error) {
	cursor := 0
	var elements []nimElement
	for {
		for cursor < len(body) && isSpace(body[cursor]) {
			cursor++
		}
		if cursor >= len(body) {
			break
		}
		el, next, err := parseNimElement(body, cursor, 1)
		if err != nil {
			return nil, err
		}
		cursor = next
		elements = append(elements, el)
	}
	return nimElementsToObject(elements, schema), nil
}

func parseNimElement(text string, cursor, depth int) (nimElement, int, error) {
	if depth > nimMaxDepth {
		return nimElement{}, 0, fmt.Errorf("excessively nested arguments")
	}
	if !strings.HasPrefix(text[cursor:], nimElemStart) || strings.HasPrefix(text[cursor:], nimElemEnd) {
		return nimElement{}, 0, fmt.Errorf("malformed argument tag")
	}
	nameStart := cursor + len(nimElemStart)
	tagEnd := strings.IndexByte(text[nameStart:], '>')
	if tagEnd < 0 {
		return nimElement{}, 0, fmt.Errorf("incomplete argument tag")
	}
	tagEnd += nameStart
	name := text[nameStart:tagEnd]
	if name == "" || strings.TrimSpace(name) != name {
		return nimElement{}, 0, fmt.Errorf("invalid argument name")
	}
	closeTag := nimElemEnd + name + ">"
	cursor = tagEnd + 1
	var textParts []string
	var children []nimElement
	for {
		marker := strings.Index(text[cursor:], nimNamespace)
		if marker < 0 {
			return nimElement{}, 0, fmt.Errorf("incomplete argument")
		}
		marker += cursor
		textParts = append(textParts, text[cursor:marker])
		if strings.HasPrefix(text[marker:], closeTag) {
			cursor = marker + len(closeTag)
			break
		}
		if strings.HasPrefix(text[marker:], nimElemStart) && !strings.HasPrefix(text[marker:], nimElemEnd) {
			child, next, err := parseNimElement(text, marker, depth+1)
			if err != nil {
				return nimElement{}, 0, err
			}
			children = append(children, child)
			cursor = next
			continue
		}
		return nimElement{}, 0, fmt.Errorf("mismatched argument tags")
	}
	return nimElement{name: name, text: strings.Join(textParts, ""), children: children}, cursor, nil
}

func nimElementsToObject(elements []nimElement, schema map[string]any) map[string]any {
	props, _ := schema["properties"].(map[string]any)
	out := map[string]any{}
	for _, el := range elements {
		ps, _ := props[el.name].(map[string]any)
		out[el.name] = nimElementValue(el, ps)
	}
	return out
}

func nimElementValue(el nimElement, schema map[string]any) any {
	declared, _ := schema["type"].(string)
	if len(el.children) > 0 {
		if declared == "array" {
			itemSchema, _ := schema["items"].(map[string]any)
			var arr []any
			for _, c := range el.children {
				arr = append(arr, nimElementValue(c, itemSchema))
			}
			return arr
		}
		res := nimElementsToObject(el.children, schema)
		if strings.TrimSpace(el.text) != "" {
			res["$text"] = el.text
		}
		return res
	}
	return nimCoerce(el.text, declared)
}

func nimCoerce(text, typ string) any {
	switch typ {
	case "integer":
		if n, err := strconv.ParseInt(strings.TrimSpace(text), 10, 64); err == nil {
			return n
		}
	case "number":
		if f, err := strconv.ParseFloat(strings.TrimSpace(text), 64); err == nil {
			return f
		}
	case "boolean":
		switch strings.TrimSpace(text) {
		case "true":
			return true
		case "false":
			return false
		}
	case "array", "object":
		var v any
		if json.Unmarshal([]byte(text), &v) == nil {
			return v
		}
	}
	return text
}

func isSpace(b byte) bool { return b == ' ' || b == '\t' || b == '\r' || b == '\n' }

// nimNativeDeltas converts one text delta into visible text + tool deltas.
func (a *OpenAIAdapter) nimNativeDeltas(text string) []streaming.Delta {
	if a.nimFramer == nil {
		a.nimFramer = &miniMaxFramer{}
	}
	visible, block, done := a.nimFramer.Feed(text)
	var out []streaming.Delta
	if visible != "" {
		out = append(out, streaming.TextDelta{Text: visible})
	}
	if done && block != "" {
		out = append(out, a.nimToolDeltas(block)...)
	}
	return out
}

func (a *OpenAIAdapter) nimToolDeltas(block string) []streaming.Delta {
	calls, err := parseNimToolBlock(block, a.nimSchemas, a.nimAliases)
	if err != nil {
		return nil
	}
	var out []streaming.Delta
	for i, c := range calls {
		args, _ := json.Marshal(c.args)
		out = append(out,
			streaming.ToolCallOpen{Index: i, ID: fmt.Sprintf("call_nim_%d", i), Name: c.name},
			streaming.ToolCallArgsDelta{Index: i, ArgsFragment: string(args)},
			streaming.ToolCallClose{Index: i},
		)
	}
	return out
}
