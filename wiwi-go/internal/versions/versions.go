package versions

import (
	"encoding/json"
	"io"
	"net/http"
	"regexp"
	"sync"
	"time"
)

// Live client-version helpers. Cline, WorkBuddy and OpenCode gate on the
// caller's User-Agent, so each is read from the npm registry (the CLI's
// distribution source of truth), cached with a 5-minute TTL, and refreshed by a
// background sweep. The synchronous header path only reads the cache.

const (
	ttlS             = 300.0
	tickS            = 300.0
	firstSweepDelayS = 10.0
	fetchTimeoutS    = 10.0
)

type source struct {
	url      string
	fallback string
}

var sources = map[string]source{
	"opencode":   {"https://registry.npmjs.org/opencode-ai/latest", "1.19.0"},
	"cline":      {"https://registry.npmjs.org/cline/latest", "unknown"},
	"cline_core": {"https://registry.npmjs.org/@cline/core/latest", "unknown"},
	"workbuddy":  {"https://registry.npmjs.org/@tencent-ai/codebuddy-code/latest", "unknown"},
}

var (
	mu     sync.RWMutex
	cached = map[string]string{}
)

var headerSafe = regexp.MustCompile(`[^\r\n\x00]`)

func clean(v string) string {
	v = headerSafe.FindString(v)
	if len(v) > 256 {
		v = v[:256]
	}
	return v
}

// Get returns the cached version (fallback before the first successful fetch).
func Get(name string) string {
	mu.RLock()
	v := cached[name]
	mu.RUnlock()
	if v != "" {
		return v
	}
	if s, ok := sources[name]; ok {
		return s.fallback
	}
	return "unknown"
}

// OpenCodeUA is the User-Agent the Zen gate expects.
func OpenCodeUA() string { return "opencode/" + Get("opencode") }

// ClineUA identifies the Cline CLI.
func ClineUA() string { return "cline/" + Get("cline") }

// ClineCoreVersion is the X-CORE-VERSION value.
func ClineCoreVersion() string { return Get("cline_core") }

// WorkBuddyUA identifies the CodeBuddy CLI.
func WorkBuddyUA() string {
	v := Get("workbuddy")
	return "CLI/" + v + " CodeBuddy/" + v
}

func fetch(name string) {
	s, ok := sources[name]
	if !ok {
		return
	}
	client := &http.Client{Timeout: time.Duration(fetchTimeoutS * float64(time.Second))}
	resp, err := client.Get(s.url)
	if err != nil {
		return
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return
	}
	body, _ := io.ReadAll(resp.Body)
	var payload struct {
		Version string `json:"version"`
	}
	if json.Unmarshal(body, &payload) != nil || payload.Version == "" {
		return
	}
	mu.Lock()
	cached[name] = clean(payload.Version)
	mu.Unlock()
}

// Start refreshes every source once after a short delay, then on the cadence.
func Start(stop <-chan struct{}) {
	go func() {
		select {
		case <-time.After(time.Duration(firstSweepDelayS * float64(time.Second))):
		case <-stop:
			return
		}
		for name := range sources {
			fetch(name)
		}
		ticker := time.NewTicker(time.Duration(tickS * float64(time.Second)))
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				for name := range sources {
					fetch(name)
				}
			case <-stop:
				return
			}
		}
	}()
}
