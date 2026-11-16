package cache

import (
	"fmt"
	"sync"
)

type flightCall struct {
	done chan struct{}
	val  []byte
	err  error
}

// Flight collapses concurrent calls for the same key into one execution: the
// first caller runs fn, everyone arriving while it runs waits for and shares
// its result. It is safe for concurrent use.
type Flight struct {
	mu    sync.Mutex
	calls map[string]*flightCall
}

// NewFlight returns an empty de-duplicator.
func NewFlight() *Flight {
	return &Flight{calls: make(map[string]*flightCall)}
}

// Do runs fn once per in-flight key and returns a copy of its result to every
// caller. A panicking fn is re-raised in the leader after the waiters are
// released with an error, so no caller can block forever.
func (f *Flight) Do(key string, fn func() ([]byte, error)) ([]byte, error) {
	f.mu.Lock()
	if c, ok := f.calls[key]; ok {
		f.mu.Unlock()
		<-c.done
		return cloneBytes(c.val), c.err
	}
	c := &flightCall{done: make(chan struct{})}
	f.calls[key] = c
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
	return cloneBytes(c.val), c.err
}
