package metrics

import (
	"strings"
	"testing"
)

func TestRender(t *testing.T) {
	c := NewCollector()
	c.Inc("wiwi_requests_total", 2)
	c.Set("wiwi_up", 1)
	out := c.Render()
	if !strings.Contains(out, "# TYPE wiwi_requests_total counter") ||
		!strings.Contains(out, "wiwi_requests_total 2") ||
		!strings.Contains(out, "# TYPE wiwi_up gauge") {
		t.Fatalf("render:\n%s", out)
	}
}
