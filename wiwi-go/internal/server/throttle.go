package server

// Per-IP abuse throttling. Ported from the Python _AttemptThrottle: a
// fixed-window attempt counter keyed by a caller-controlled scope (client IP,
// possibly plus the normalized account). Deliberately not built on the
// rate limiter: that reserves a slot on every check, whereas login/registration
// throttles must only count failures, so a genuine user is never locked out by
// their own valid traffic.

import (
	"net/netip"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
)

const throttleMaxKeys = 10_000

type attemptThrottle struct {
	mu      sync.Mutex
	limit   int
	window  time.Duration
	maxKeys int
	hits    map[string][]time.Time
}

func newAttemptThrottle(limit int, window time.Duration) *attemptThrottle {
	if limit < 1 {
		limit = 1
	}
	if window <= 0 {
		window = time.Minute
	}
	return &attemptThrottle{
		limit: limit, window: window, maxKeys: throttleMaxKeys,
		hits: map[string][]time.Time{},
	}
}

// TryConsume atomically admits one attempt or refuses it. Reserving and
// counting under one lock is what makes the cap hold under concurrency: a
// check-then-record pair lets a burst pass before any of it is recorded.
func (t *attemptThrottle) TryConsume(scope string) (bool, int) {
	t.mu.Lock()
	defer t.mu.Unlock()
	now := time.Now()
	cutoff := now.Add(-t.window)
	evs := make([]time.Time, 0, len(t.hits[scope]))
	for _, at := range t.hits[scope] {
		if at.After(cutoff) {
			evs = append(evs, at)
		}
	}
	if len(evs) >= t.limit {
		retry := int(maxFloat(1, t.window.Seconds()-now.Sub(evs[0]).Seconds())) + 1
		t.hits[scope] = evs
		return false, retry
	}
	evs = append(evs, now)
	t.hits[scope] = evs
	if len(t.hits) > t.maxKeys {
		t.pruneStale(now)
	}
	return true, 0
}

// Reset clears a scope after a successful attempt, returning the reserved slot.
func (t *attemptThrottle) Reset(scope string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	delete(t.hits, scope)
}

// Refund gives back one reservation taken by TryConsume.
func (t *attemptThrottle) Refund(scope string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	evs := t.hits[scope]
	if len(evs) > 0 {
		evs = evs[:len(evs)-1]
		if len(evs) == 0 {
			delete(t.hits, scope)
		} else {
			t.hits[scope] = evs
		}
	}
}

func (t *attemptThrottle) pruneStale(now time.Time) {
	cutoff := now.Add(-t.window)
	for scope, evs := range t.hits {
		alive := make([]time.Time, 0, len(evs))
		for _, at := range evs {
			if at.After(cutoff) {
				alive = append(alive, at)
			}
		}
		if len(alive) == 0 {
			delete(t.hits, scope)
		} else {
			t.hits[scope] = alive
		}
	}
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

// clientIP is the best-effort client address used to key abuse throttles.
// X-Forwarded-For is trusted only when the direct peer is in trusted_proxies;
// otherwise an attacker could mint a fresh throttle bucket per request by
// rotating the header (Python AUDIT #73). It is never used for authn or URLs.
func clientIP(c *gin.Context, trusted []string) string {
	peer := peerHost(c.Request.RemoteAddr)
	fwd := c.GetHeader("X-Forwarded-For")
	if fwd != "" && len(trusted) > 0 && peerTrusted(peer, trusted) {
		if first := strings.TrimSpace(strings.Split(fwd, ",")[0]); first != "" {
			return first
		}
	}
	if peer == "" {
		return "unknown"
	}
	return peer
}

// peerHost extracts the host from a net.RemoteAddr-style "host:port".
func peerHost(remote string) string {
	if remote == "" {
		return ""
	}
	if addr, err := netip.ParseAddrPort(remote); err == nil {
		return addr.Addr().String()
	}
	if addr, err := netip.ParseAddr(remote); err == nil {
		return addr.String()
	}
	// Unusual form: keep the original rather than misclassifying it.
	return remote
}

// peerTrusted reports whether peer falls inside any trusted-proxy CIDR or
// exact address.
func peerTrusted(peer string, trusted []string) bool {
	addr, err := netip.ParseAddr(peer)
	if err != nil {
		return false
	}
	addr = addr.Unmap()
	for _, entry := range trusted {
		entry = strings.TrimSpace(entry)
		if entry == "" {
			continue
		}
		if strings.Contains(entry, "/") {
			if prefix, err := netip.ParsePrefix(entry); err == nil && prefix.Contains(addr) {
				return true
			}
			continue
		}
		if other, err := netip.ParseAddr(entry); err == nil && other.Unmap() == addr {
			return true
		}
	}
	return false
}

// requestIsHTTPS reports whether the caller reached the gateway over TLS,
// believing X-Forwarded-Proto only from a trusted peer (same gate as clientIP).
func requestIsHTTPS(c *gin.Context, trusted []string) bool {
	if c.Request.TLS != nil {
		return true
	}
	fwd := c.GetHeader("X-Forwarded-Proto")
	if fwd == "" || len(trusted) == 0 {
		return false
	}
	if !peerTrusted(peerHost(c.Request.RemoteAddr), trusted) {
		return false
	}
	first := strings.ToLower(strings.TrimSpace(strings.Split(fwd, ",")[0]))
	return first == "https"
}
