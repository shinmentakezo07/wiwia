package providers

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
)

// DoRequest performs a non-streaming upstream POST and returns body + status.
func DoRequest(url string, headers map[string]string, body any, timeoutSec float64) ([]byte, int, error) {
	b, _ := json.Marshal(body)
	req, err := http.NewRequest("POST", url, bytes.NewReader(b))
	if err != nil {
		return nil, 0, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	client := HTTPClient(timeoutSec)
	resp, err := client.Do(req)
	if err != nil {
		return nil, 0, err
	}
	defer resp.Body.Close()
	respBody, _ := io.ReadAll(resp.Body)
	return respBody, resp.StatusCode, nil
}

// DoStream opens a streaming upstream POST and returns the live response.
func DoStream(url string, headers map[string]string, body any) (*http.Response, error) {
	b, _ := json.Marshal(body)
	req, err := http.NewRequest("POST", url, bytes.NewReader(b))
	if err != nil {
		return nil, err
	}
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	req.Header.Set("Accept", "text/event-stream")
	return HTTPClient(0).Do(req)
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
