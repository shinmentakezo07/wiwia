package providers

import (
	"net/http"
	"sync"
	"time"
)

var (
	clientMu     sync.Mutex
	sharedClient *http.Client
	poolMaxConns = 200
	poolMaxIdle  = 40
	poolIdleTTL  = 30 * time.Second
)

// ConfigureHTTPPool sets the outbound connection pool. It mirrors the Python
// httpx limits (total connections, keepalive connections, keepalive expiry).
// Zero values keep the previous setting.
func ConfigureHTTPPool(maxConns, maxKeepalive int, idleTimeout time.Duration) {
	clientMu.Lock()
	defer clientMu.Unlock()
	if maxConns > 0 {
		poolMaxConns = maxConns
	}
	if maxKeepalive > 0 {
		poolMaxIdle = maxKeepalive
	}
	if idleTimeout > 0 {
		poolIdleTTL = idleTimeout
	}
	sharedClient = nil
}

// HTTPClient returns a pooled client. timeoutSec<=0 means no client timeout
// (streams are governed by the pump's watchdogs instead).
func HTTPClient(timeoutSec float64) *http.Client {
	clientMu.Lock()
	defer clientMu.Unlock()
	if sharedClient == nil {
		sharedClient = &http.Client{
			Transport: &http.Transport{
				MaxIdleConns:        poolMaxConns,
				MaxIdleConnsPerHost: poolMaxIdle,
				MaxConnsPerHost:     poolMaxConns,
				IdleConnTimeout:     poolIdleTTL,
			},
		}
	}
	c := *sharedClient
	if timeoutSec > 0 {
		c.Timeout = time.Duration(timeoutSec * float64(time.Second))
	} else {
		c.Timeout = 0
	}
	return &c
}
