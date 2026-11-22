package telemetry

import (
	"math/rand"
	"reflect"
	"time"

	"wiwi-go/internal/logging"
)

// Tracer is a lightweight span recorder. It is a no-op when disabled (the
// default), so tracing can never become a serving dependency. When enabled it
// publishes one "span" event per span onto the logging bus, which the live tail
// and admin proxy logs surface. This replaces the optional OTel SDK with an
// in-process recorder that needs no extra dependency.
type Tracer struct {
	enabled  bool
	bus      *logging.Bus
	service  string
	exporter *otlpExporter
	sample   float64
}

func New(enabled bool, bus *logging.Bus, service string) *Tracer {
	if service == "" {
		service = "wiwi"
	}
	return &Tracer{enabled: enabled, bus: bus, service: service, sample: 1}
}

// NewWithOTLP enables the OTLP/HTTP JSON exporter when endpoint is set.
func NewWithOTLP(enabled bool, bus *logging.Bus, service, endpoint string, headers map[string]string, sample float64, exportTimeoutS float64) *Tracer {
	t := New(enabled, bus, service)
	// 0 is a legitimate ratio (export nothing), not "unset": silently turning
	// it into 1 would export every span of a deployment that asked for none.
	if sample < 0 {
		sample = 0
	}
	if sample > 1 {
		sample = 1
	}
	t.sample = sample
	if enabled && endpoint != "" {
		t.exporter = newOTLPExporter(endpoint, headers, time.Duration(exportTimeoutS*float64(time.Second)))
	}
	return t
}

func (t *Tracer) Enabled() bool { return t != nil && t.enabled }

// Span is one recorded operation.
type Span struct {
	tracer  *Tracer
	Name    string
	Start   time.Time
	Attrs   map[string]any
	TraceID string
	SpanID  string
}

// Start opens a span. A disabled tracer returns a span whose End is a no-op.
func (t *Tracer) Start(name string, attrs map[string]any) *Span {
	if !t.Enabled() {
		return &Span{tracer: t, Name: name}
	}
	a := map[string]any{}
	for k, v := range attrs {
		a[k] = v
	}
	return &Span{
		tracer: t, Name: name, Start: time.Now(), Attrs: a,
		TraceID: newHexID(16), SpanID: newHexID(8),
	}
}

func (s *Span) SetAttr(key string, value any) {
	if s == nil || s.Attrs == nil {
		return
	}
	s.Attrs[key] = value
}

// End closes the span and publishes it (if tracing is enabled).
func (s *Span) End(err error) {
	if s == nil || s.tracer == nil || !s.tracer.Enabled() {
		return
	}
	data := map[string]any{
		"service":     s.tracer.service,
		"span":        s.Name,
		"duration_ms": time.Since(s.Start).Milliseconds(),
	}
	for k, v := range s.Attrs {
		data[k] = v
	}
	if err != nil && !isNilErr(err) {
		data["error"] = err.Error()
	}
	s.tracer.bus.Publish("span", data)
	// telemetry.sample_ratio is the trace sampler (Python's TraceIdRatioBased):
	// a span outside the sample is gauged locally but not exported.
	if s.tracer.exporter != nil && !s.tracer.sampled() {
		errMsg := ""
		if err != nil && !isNilErr(err) {
			errMsg = err.Error()
		}
		s.tracer.exporter.export(s.tracer.service, s.Name, s.TraceID, s.SpanID, s.Start, time.Now(), s.Attrs, errMsg)
	}
}

// sampled decides whether this span is exported, honoring telemetry.sample_ratio.
func (t *Tracer) sampled() bool {
	if t == nil {
		return false
	}
	if t.sample >= 1 {
		return true
	}
	if t.sample <= 0 {
		return false
	}
	return rand.Float64() < t.sample
}

// isNilErr reports whether err is nil or a typed-nil pointer wrapped in an
// error interface (a common Go footgun that would otherwise panic on Error()).
func isNilErr(err error) bool {
	if err == nil {
		return true
	}
	v := reflect.ValueOf(err)
	switch v.Kind() {
	case reflect.Ptr, reflect.Interface, reflect.Map, reflect.Slice, reflect.Func, reflect.Chan:
		return v.IsNil()
	}
	return false
}
