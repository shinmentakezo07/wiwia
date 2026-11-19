package telemetry

import (
	"bytes"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strconv"
	"time"
)

// otlpExporter posts spans to an OTLP/HTTP endpoint using the JSON encoding
// (content-type application/json), which collectors accept at /v1/traces.
type otlpExporter struct {
	endpoint string
	headers  map[string]string
	client   *http.Client
}

func newOTLPExporter(endpoint string, headers map[string]string, timeout time.Duration) *otlpExporter {
	if timeout <= 0 {
		timeout = 10 * time.Second
	}
	return &otlpExporter{
		endpoint: endpoint,
		headers:  headers,
		client:   &http.Client{Timeout: timeout},
	}
}

func newHexID(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func attr(key string, value any) map[string]any {
	switch v := value.(type) {
	case string:
		return map[string]any{"key": key, "value": map[string]any{"stringValue": v}}
	case bool:
		return map[string]any{"key": key, "value": map[string]any{"boolValue": v}}
	case int:
		return map[string]any{"key": key, "value": map[string]any{"intValue": strconv.Itoa(v)}}
	case int64:
		return map[string]any{"key": key, "value": map[string]any{"intValue": strconv.FormatInt(v, 10)}}
	case float64:
		return map[string]any{"key": key, "value": map[string]any{"doubleValue": v}}
	default:
		b, _ := json.Marshal(v)
		return map[string]any{"key": key, "value": map[string]any{"stringValue": string(b)}}
	}
}

// export sends one span. Failures are ignored: tracing must never break serving.
func (e *otlpExporter) export(service, name, traceID, spanID string, start, end time.Time, attrs map[string]any, errMsg string) {
	attributes := make([]any, 0, len(attrs))
	for k, v := range attrs {
		attributes = append(attributes, attr(k, v))
	}
	span := map[string]any{
		"traceId":           traceID,
		"spanId":            spanID,
		"name":              name,
		"kind":              1,
		"startTimeUnixNano": strconv.FormatInt(start.UnixNano(), 10),
		"endTimeUnixNano":   strconv.FormatInt(end.UnixNano(), 10),
		"attributes":        attributes,
	}
	if errMsg != "" {
		span["status"] = map[string]any{"code": 2, "message": errMsg}
	}
	payload := map[string]any{
		"resourceSpans": []any{map[string]any{
			"resource": map[string]any{"attributes": []any{
				attr("service.name", service),
			}},
			"scopeSpans": []any{map[string]any{
				"scope": map[string]any{"name": "wiwi"},
				"spans": []any{span},
			}},
		}},
	}
	body, _ := json.Marshal(payload)
	req, err := http.NewRequest("POST", e.endpoint, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")
	for k, v := range e.headers {
		req.Header.Set(k, v)
	}
	go func() {
		resp, err := e.client.Do(req)
		if err == nil && resp != nil {
			resp.Body.Close()
		}
	}()
}
