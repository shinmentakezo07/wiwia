package metrics

import (
	"fmt"
	"sort"
	"strings"
	"sync"
)

// Collector is a tiny thread-safe Prometheus-text collector. It keeps counters
// and gauges by name and renders the standard text exposition format.
type Collector struct {
	mu       sync.Mutex
	counters map[string]float64
	gauges   map[string]float64
}

func NewCollector() *Collector {
	return &Collector{counters: map[string]float64{}, gauges: map[string]float64{}}
}

func (c *Collector) Inc(name string, v float64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.counters[sanitize(name)] += v
}

func (c *Collector) Set(name string, v float64) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.gauges[sanitize(name)] = v
}

func (c *Collector) Get(name string) float64 {
	c.mu.Lock()
	defer c.mu.Unlock()
	n := sanitize(name)
	if v, ok := c.counters[n]; ok {
		return v
	}
	return c.gauges[n]
}

// Render produces a valid Prometheus text exposition body.
func (c *Collector) Render() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	var b strings.Builder
	write := func(kind string, m map[string]float64) {
		names := make([]string, 0, len(m))
		for k := range m {
			names = append(names, k)
		}
		sort.Strings(names)
		for _, k := range names {
			fmt.Fprintf(&b, "# TYPE %s %s\n%s %g\n", k, kind, k, m[k])
		}
	}
	write("counter", c.counters)
	write("gauge", c.gauges)
	return b.String()
}

func sanitize(name string) string {
	if name == "" {
		return "wiwi_metric"
	}
	var b strings.Builder
	for i, r := range name {
		ok := r == '_' || (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9')
		if !ok || (i == 0 && r >= '0' && r <= '9') {
			b.WriteByte('_')
			continue
		}
		b.WriteRune(r)
	}
	return b.String()
}
