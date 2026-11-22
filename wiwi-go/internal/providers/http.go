package providers

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"
)

// DoRequest performs a non-streaming upstream POST and returns body + status.
func DoRequest(url string, headers map[string]string, body any, timeoutSec float64) ([]byte, int, error) {
	b, status, _, err := DoRequestHeadersCtx(context.Background(), url, headers, body, timeoutSec)
	return b, status, err
}

// DoRequestHeadersCtx is DoRequest with a caller context (so a client hangup
// cancels the upstream call) and the response headers, which carry the
// upstream's Retry-After cooling hint.
func DoRequestHeadersCtx(ctx context.Context, url string, headers map[string]string, body any, timeoutSec float64) ([]byte, int, http.Header, error) {
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(b))
	if err != nil {
		return nil, 0, nil, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := HTTPClient(timeoutSec)
	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, nil, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	return respBody, resp.StatusCode, resp.Header.Clone(), nil
}

// DoStream opens a streaming upstream POST and returns the live response.
func DoStream(url string, headers map[string]string, body any) (*http.Response, error) {
	return DoStreamCtx(context.Background(), url, headers, body)
}

// DoStreamCtx is DoStream with a caller context, so the pump's watchdog can
// cancel a stalled upstream read.
func DoStreamCtx(ctx context.Context, url string, headers map[string]string, body any) (*http.Response, error) {
	b, _ := json.Marshal(body)
	req, err := http.NewRequestWithContext(ctx, "POST", url, bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	req.Header.Set("Accept", "text/event-stream")
	return HTTPClient(0).Do(req)
}

// RetryAfterSeconds reads an upstream cooling hint from Retry-After or the
// rate-limit reset headers vendors use instead of it. Zero means "no hint".
func RetryAfterSeconds(h http.Header) float64 {
	if h == nil {
		return 0
	}
	if v := strings.TrimSpace(h.Get("Retry-After")); v != "" {
		if secs, err := strconv.ParseFloat(v, 64); err == nil {
			if secs < 0 {
				return 0
			}
			return secs
		}
		// Retry-After may be an HTTP date instead of a delta.
		if t, err := http.ParseTime(v); err == nil {
			if d := time.Until(t).Seconds(); d > 0 {
				return d
			}
		}
		return 0
	}
	for _, key := range []string{"X-RateLimit-Reset", "x-ratelimit-reset"} {
		if v := strings.TrimSpace(h.Get(key)); v != "" {
			if secs, err := strconv.ParseFloat(v, 64); err == nil {
				// Some vendors send a delta, most send an epoch timestamp.
				now := float64(time.Now().Unix())
				if secs > now {
					return secs - now
				}
				if secs > 0 {
					return secs
				}
			}
		}
	}
	if v := strings.TrimSpace(h.Get("X-RateLimit-Reset-Requests")); v != "" {
		if d, ok := ParseDurationSeconds(v); ok {
			return d
		}
	}
	return 0
}

// ParseDurationSeconds parses a compact duration hint ("6m0s", "1.5s", "500ms").
// ok is false when the value is not a duration.
func ParseDurationSeconds(v string) (float64, bool) {
	if d, err := time.ParseDuration(v); err == nil {
		return d.Seconds(), true
	}
	if secs, err := strconv.ParseFloat(v, 64); err == nil {
		return secs, true
	}
	return 0, false
}
