package providers

import (
	"net/http"
	"sync"
	"time"
)

var (
	clientMu     sync.Mutex
	sharedClient *http.Client
)

// HTTPClient returns a pooled client. timeoutSec<=0 means no client timeout
// (streams are governed by the pump's watchdogs instead).
func HTTPClient(timeoutSec float64) *http.Client {
	clientMu.Lock()
	defer clientMu.Unlock()
	if sharedClient == nil {
		sharedClient = &http.Client{
			Transport: &http.Transport{
				MaxIdleConns:        200,
				MaxIdleConnsPerHost: 40,
				IdleConnTimeout:     30 * time.Second,
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
