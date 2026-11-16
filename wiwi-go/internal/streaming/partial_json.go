package streaming

import (
	"encoding/json"
	"regexp"
	"strings"
)

// Incremental JSON parser for streaming tool-call arguments. Parses incomplete
// JSON as it arrives and auto-repairs truncated JSON at close time instead of
// dropping args to {}.

const (
	maxBufferBytes = 1024 * 1024
	maxRepairDepth = 4096
)

var (
	danglingNumber  = regexp.MustCompile(`[0-9][0-9.eE+\-]*[.eE+\-]$`)
	trailingString  = regexp.MustCompile(`"(?:[^"\\]|\\.)*"$`)
	trailingLetters = regexp.MustCompile(`[A-Za-z]+$`)
	partialEscape   = regexp.MustCompile(`(\\+)u[0-9a-fA-F]{0,3}$`)
	completeEscape  = regexp.MustCompile(`(\\+)u([0-9a-fA-F]{4})$`)
)

var openClose = map[byte]byte{'{': '}', '[': ']', '(': ')'}

var partialLiterals = map[string]string{
	"t": "true", "tr": "true", "tru": "true",
	"f": "false", "fa": "false", "fal": "false", "fals": "false",
	"n": "null", "nu": "null", "nul": "null",
}

type jsonScan struct {
	stack     []byte
	inString  bool
	openIdx   int
	openIsKey bool
}

func scanJSON(text string) jsonScan {
	res := jsonScan{openIdx: -1}
	var prevSig byte
	escaped := false
	for idx := 0; idx < len(text); idx++ {
		ch := text[idx]
		if res.inString {
			if escaped {
				escaped = false
			} else if ch == '\\' {
				escaped = true
			} else if ch == '"' {
				res.inString = false
				prevSig = ch
			}
			continue
		}
		if ch == '"' {
			res.inString = true
			res.openIdx = idx
			res.openIsKey = (prevSig == '{' || prevSig == ',') && len(res.stack) > 0 && res.stack[len(res.stack)-1] == '}'
		} else if closer, ok := openClose[ch]; ok {
			if len(res.stack) < maxRepairDepth {
				res.stack = append(res.stack, closer)
			}
			prevSig = ch
		} else if isCloseChar(ch) && len(res.stack) > 0 && res.stack[len(res.stack)-1] == ch {
			res.stack = res.stack[:len(res.stack)-1]
			prevSig = ch
		} else if ch != ' ' && ch != '\t' && ch != '\n' && ch != '\r' {
			prevSig = ch
		}
	}
	return res
}

func isCloseChar(ch byte) bool { return ch == '}' || ch == ']' || ch == ')' }

func trimIncompleteValue(text string, stack []byte) string {
	for {
		text = strings.TrimRight(text, " \t\r\n")
		if text == "" {
			return text
		}
		last := text[len(text)-1]
		if last == ',' || last == ':' {
			text = text[:len(text)-1]
			continue
		}
		if last == '+' || last == '-' {
			text = text[:len(text)-1]
			continue
		}
		if m := danglingNumber.FindStringIndex(text); m != nil {
			text = text[:m[0]]
			continue
		}
		if m := trailingLetters.FindStringIndex(text); m != nil {
			token := text[m[0]:m[1]]
			completion, ok := partialLiterals[token]
			if !ok {
				return text
			}
			return text[:m[0]] + completion
		}
		if last == '"' {
			if m := trailingString.FindStringIndex(text); m != nil {
				before := strings.TrimRight(text[:m[0]], " \t\r\n")
				if before != "" && (before[len(before)-1] == '{' || before[len(before)-1] == ',') &&
					len(stack) > 0 && stack[len(stack)-1] == '}' {
					text = before
					continue
				}
			}
			return text
		}
		return text
	}
}

func repairTruncatedJSON(text string) string {
	scan := scanJSON(text)
	for scan.inString && scan.openIsKey {
		text = strings.TrimRight(text[:scan.openIdx], " \t\r\n")
		scan = scanJSON(text)
	}
	var suffix strings.Builder
	if scan.inString {
		n := len(text) - len(strings.TrimRight(text, "\\"))
		if n%2 == 1 {
			text = text[:len(text)-1]
		}
		if m := partialEscape.FindStringSubmatchIndex(text); m != nil {
			runLen := m[3] - m[2]
			if runLen%2 == 1 {
				text = text[:m[0]]
			}
		}
		if m := completeEscape.FindStringSubmatchIndex(text); m != nil {
			runLen := m[3] - m[2]
			if runLen%2 == 1 {
				keep := text[m[2] : m[3]-1]
				code := hexVal(text[m[4]:m[5]])
				if code >= 0xD800 && code <= 0xDBFF {
					text = text[:m[0]] + keep
				} else if code >= 0xDC00 && code <= 0xDFFF {
					prev := completeEscape.FindStringSubmatchIndex(text[:m[0]])
					paired := prev != nil && (prev[3]-prev[2])%2 == 1 &&
						hexVal(text[prev[4]:prev[5]]) >= 0xD800 && hexVal(text[prev[4]:prev[5]]) <= 0xDBFF
					if !paired {
						text = text[:m[0]] + keep
					}
				}
			}
		}
		suffix.WriteByte('"')
	} else {
		text = trimIncompleteValue(text, scan.stack)
	}
	for i := len(scan.stack) - 1; i >= 0; i-- {
		suffix.WriteByte(scan.stack[i])
	}
	return text + suffix.String()
}

func hexVal(s string) int {
	n := 0
	for _, r := range s {
		n *= 16
		switch {
		case r >= '0' && r <= '9':
			n += int(r - '0')
		case r >= 'a' && r <= 'f':
			n += int(r-'a') + 10
		case r >= 'A' && r <= 'F':
			n += int(r-'A') + 10
		}
	}
	return n
}

// ParsePartial parses potentially-incomplete JSON. Returns (value, complete).
// Never panics on truncated/malformed input; ({}, false) is the fallback.
func ParsePartial(text string) (any, bool) {
	text = strings.TrimSpace(text)
	if text == "" {
		return map[string]any{}, false
	}
	var v any
	if json.Unmarshal([]byte(text), &v) == nil {
		return v, true
	}
	repaired := repairTruncatedJSON(text)
	if json.Unmarshal([]byte(repaired), &v) == nil {
		return v, false
	}
	return map[string]any{}, false
}

// PartialJSONParser is a stateful accumulator for streaming tool args.
type PartialJSONParser struct {
	buf string
}

// Feed accumulates a fragment and returns the best-effort parsed value.
func (p *PartialJSONParser) Feed(fragment string) any {
	if fragment == "" {
		v, _ := ParsePartial(p.buf)
		return v
	}
	if len(fragment) >= maxBufferBytes {
		p.buf = fragment[len(fragment)-maxBufferBytes:]
	} else {
		p.buf += fragment
		if overflow := len(p.buf) - maxBufferBytes; overflow > 0 {
			p.buf = p.buf[overflow:]
		}
	}
	v, _ := ParsePartial(p.buf)
	return v
}

// Finalize repairs and parses the accumulated buffer. Always returns a map.
func (p *PartialJSONParser) Finalize() map[string]any {
	if strings.TrimSpace(p.buf) == "" {
		return map[string]any{}
	}
	repaired := repairTruncatedJSON(strings.TrimSpace(p.buf))
	var v any
	if json.Unmarshal([]byte(repaired), &v) == nil {
		if m, ok := v.(map[string]any); ok {
			return m
		}
		return map[string]any{"value": v}
	}
	return map[string]any{}
}

func (p *PartialJSONParser) Raw() string { return p.buf }
func (p *PartialJSONParser) Reset()      { p.buf = "" }

// RepairArgs parses raw tool-call arguments, repairing truncated JSON. It is
// the one-call form used by the decode-side repairs.
func RepairArgs(raw string) map[string]any {
	if strings.TrimSpace(raw) == "" {
		return map[string]any{}
	}
	var v any
	if json.Unmarshal([]byte(raw), &v) == nil {
		if m, ok := v.(map[string]any); ok {
			return m
		}
	}
	repaired := repairTruncatedJSON(strings.TrimSpace(raw))
	if json.Unmarshal([]byte(repaired), &v) == nil {
		if m, ok := v.(map[string]any); ok {
			return m
		}
		return map[string]any{"value": v}
	}
	return map[string]any{}
}
