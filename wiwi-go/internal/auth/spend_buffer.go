package auth

import (
	"sync"
	"time"
)

type pendingSpend struct {
	reserved float64
	cost     float64
}

// SpendBuffer aggregates per-key reservation releases and charges into one
// settlement per flush (spend_batching_enabled). The reservation stays in the
// DB until the flush, so a crash fails closed.
type SpendBuffer struct {
	mu         sync.Mutex
	pending    map[string]*pendingSpend
	interval   time.Duration
	maxPending int
	maxDollars float64
}

func NewSpendBuffer(interval time.Duration, maxPending int, maxDollars float64) *SpendBuffer {
	if interval <= 0 {
		interval = 250 * time.Millisecond
	}
	if maxPending <= 0 {
		maxPending = 1000
	}
	if maxDollars <= 0 {
		maxDollars = 1
	}
	return &SpendBuffer{
		pending: map[string]*pendingSpend{}, interval: interval,
		maxPending: maxPending, maxDollars: maxDollars,
	}
}

// Add accumulates one request's reservation and cost for a key.
func (b *SpendBuffer) Add(keyID string, reserved, cost float64) {
	if keyID == "" {
		return
	}
	b.mu.Lock()
	defer b.mu.Unlock()
	p := b.pending[keyID]
	if p == nil {
		p = &pendingSpend{}
		b.pending[keyID] = p
	}
	p.reserved += reserved
	p.cost += cost
}

// flush drains and settles every pending key.
func (b *SpendBuffer) flush(svc *Service) {
	b.mu.Lock()
	batch := b.pending
	b.pending = map[string]*pendingSpend{}
	b.mu.Unlock()
	for keyID, p := range batch {
		_ = svc.Settle(keyID, p.reserved, p.cost)
	}
}

// Run flushes on the configured cadence until stop closes.
func (b *SpendBuffer) Run(svc *Service, stop <-chan struct{}) {
	ticker := time.NewTicker(b.interval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			b.flush(svc)
		case <-stop:
			b.flush(svc)
			return
		}
	}
}
