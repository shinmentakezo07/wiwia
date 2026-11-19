package cache

import (
	"fmt"
	"sync"
	"sync/atomic"

	"wiwi-go/internal/ir"
)

// Result is what a leader hands to waiters: the encoded body plus the usage it
// was priced with, so a joiner can log the shared usage at cost 0.
type Result struct {
	Payload []byte
	Usage   ir.Usage
	Cost    float64
}

type flightCall struct {
	done    chan struct{}
	val     Result
	err     error
	waiters int
}

// Flight collapses concurrent calls for the same key into one execution: the
// first caller runs fn, everyone arriving while it runs waits for and shares
// its result. It is safe for concurrent use.
//
// maxFlights/maxWaiters bound the queues the way Python's SingleFlight does:
// a saturated flight table or waiter set bypasses dedupe instead of growing
// without bound. maxWaiters==0 makes every follower run independently.
type Flight struct {
	mu         sync.Mutex
	calls      map[string]*flightCall
	maxFlights int
	maxWaiters int
	leaders    int64
	joined     int64
	bypassed   int64
}

// NewFlight returns an empty de-duplicator with the Python defaults.
func NewFlight() *Flight {
	return NewFlightWithLimits(2048, 64)
}

// NewFlightWithLimits returns a de-duplicator with explicit bounds.
func NewFlightWithLimits(maxFlights, maxWaiters int) *Flight {
	if maxFlights < 1 {
		maxFlights = 1
	}
	if maxWaiters < 0 {
		maxWaiters = 0
	}
	return &Flight{
		calls:      make(map[string]*flightCall),
		maxFlights: maxFlights,
		maxWaiters: maxWaiters,
	}
}

// Stats reports leaders, joiners, and bypassed calls.
func (f *Flight) Stats() (leaders, joined, bypassed int64) {
	return atomic.LoadInt64(&f.leaders), atomic.LoadInt64(&f.joined), atomic.LoadInt64(&f.bypassed)
}

// Do runs fn once per in-flight key and returns a copy of its payload to every
// caller. A panicking fn is re-raised in the leader after the waiters are
// released with an error, so no caller can block forever.
func (f *Flight) Do(key string, fn func() ([]byte, error)) ([]byte, error) {
	res, _, err := f.DoResult(key, func() (Result, error) {
		b, e := fn()
		return Result{Payload: b}, e
	})
	return res.Payload, err
}

// DoResult is Do with the leader's usage/cost and a joined flag.
func (f *Flight) DoResult(key string, fn func() (Result, error)) (Result, bool, error) {
	f.mu.Lock()
	if c, ok := f.calls[key]; ok {
		if c.waiters >= f.maxWaiters {
			atomic.AddInt64(&f.bypassed, 1)
			f.mu.Unlock()
			res, err := fn()
			return res, false, err
		}
		c.waiters++
		atomic.AddInt64(&f.joined, 1)
		f.mu.Unlock()
		<-c.done
		return cloneResult(c.val), true, c.err
	}
	if len(f.calls) >= f.maxFlights {
		atomic.AddInt64(&f.bypassed, 1)
		f.mu.Unlock()
		res, err := fn()
		return res, false, err
	}
	c := &flightCall{done: make(chan struct{})}
	f.calls[key] = c
	atomic.AddInt64(&f.leaders, 1)
	f.mu.Unlock()

	func() {
		defer func() {
			r := recover()
			if r != nil {
				c.err = fmt.Errorf("cache: singleflight panic: %v", r)
			}
			f.mu.Lock()
			delete(f.calls, key)
			f.mu.Unlock()
			close(c.done)
			if r != nil {
				panic(r)
			}
		}()
		c.val, c.err = fn()
	}()
	return cloneResult(c.val), false, c.err
}

func cloneResult(r Result) Result {
	r.Payload = cloneBytes(r.Payload)
	return r
}
