package streaming

import (
	"bufio"
	"io"
	"strings"
)

// Event is one parsed Server-Sent-Events frame.
type Event struct {
	ID    string
	Event string
	Data  string
}

// Frame renders one SSE frame. A blank event name is omitted.
func Frame(event, data, id string) string {
	var b strings.Builder
	if id != "" {
		b.WriteString("id: ")
		b.WriteString(id)
		b.WriteString("\n")
	}
	if event != "" {
		b.WriteString("event: ")
		b.WriteString(event)
		b.WriteString("\n")
	}
	b.WriteString("data: ")
	b.WriteString(data)
	b.WriteString("\n\n")
	return b.String()
}

// Parse reads SSE frames from r, invoking fn for each complete event. It stops
// on EOF or when fn returns false.
func Parse(r io.Reader, fn func(Event) bool) error {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 64*1024), 4*1024*1024)
	var ev Event
	var data []string
	flush := func() bool {
		if len(data) == 0 && ev.Event == "" && ev.ID == "" {
			return true
		}
		ev.Data = strings.Join(data, "\n")
		ok := fn(ev)
		ev = Event{}
		data = nil
		return ok
	}
	for sc.Scan() {
		line := sc.Text()
		if line == "" {
			if !flush() {
				return nil
			}
			continue
		}
		if strings.HasPrefix(line, ":") {
			continue // comment / keep-alive
		}
		field, val, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		val = strings.TrimPrefix(val, " ")
		switch field {
		case "data":
			data = append(data, val)
		case "event":
			ev.Event = val
		case "id":
			ev.ID = val
		}
	}
	flush()
	return sc.Err()
}
