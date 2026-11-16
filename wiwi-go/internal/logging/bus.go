// Package logging provides the in-process event bus (admin live log + replay)
// and the aggregate request totals shown on the dashboard.
package logging

import (
	"sync"
	"time"
)

// defaultCap is used when NewBus is given a non-positive capacity.
const defaultCap = 1000

// subBuffer is the per-subscriber channel buffer; a slow subscriber drops new
// events instead of blocking publishers.
const subBuffer = 256

// Event is one published log entry. ID is monotonic from 1; Ts is Unix seconds.
type Event struct {
	ID   int64          `json:"id"`
	Ts   int64          `json:"ts"`
	Type string         `json:"type"`
	Data map[string]any `json:"data,omitempty"`
}

type subscriber struct {
	ch chan Event
}

// Bus is a ring buffer of recent events plus a fan-out to live subscribers.
// It is safe for concurrent use.
type Bus struct {
	mu      sync.Mutex
	cap     int
	events  []Event // ring storage; len == cap once full
	head    int     // index of the oldest event when the ring is full
	nextID  int64
	subs    map[int64]*subscriber
	nextSub int64
}

// NewBus returns a bus retaining the last cap events (default 1000).
func NewBus(capacity int) *Bus {
	if capacity <= 0 {
		capacity = defaultCap
	}
	return &Bus{
		cap:  capacity,
		subs: make(map[int64]*subscriber),
	}
}

// Publish stores an event, assigns its ID, and delivers it to subscribers. Data
// is shallow-copied so later mutation by the caller is not visible to readers.
func (b *Bus) Publish(typ string, data map[string]any) int64 {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.nextID++
	ev := Event{ID: b.nextID, Ts: time.Now().Unix(), Type: typ, Data: cloneData(data)}
	if len(b.events) < b.cap {
		b.events = append(b.events, ev)
	} else {
		b.events[b.head] = ev
		b.head = (b.head + 1) % b.cap
	}
	for _, s := range b.subs {
		select {
		case s.ch <- ev:
		default: // subscriber is behind; drop this event for it
		}
	}
	return ev.ID
}

// EventsSince returns the buffered events with ID > id, oldest first.
func (b *Bus) EventsSince(id int64) []Event {
	b.mu.Lock()
	defer b.mu.Unlock()
	out := make([]Event, 0, len(b.events))
	for _, ev := range b.orderedLocked() {
		if ev.ID > id {
			out = append(out, ev)
		}
	}
	return out
}

// Subscribe replays buffered events with ID > since and then streams new ones.
// The returned cancel stops delivery and closes the channel; it is idempotent.
func (b *Bus) Subscribe(since int64) (<-chan Event, func()) {
	b.mu.Lock()
	defer b.mu.Unlock()
	ch := make(chan Event, subBuffer)
	id := b.nextSub
	b.nextSub++
	b.subs[id] = &subscriber{ch: ch}

	backlog := make([]Event, 0, len(b.events))
	for _, ev := range b.orderedLocked() {
		if ev.ID > since {
			backlog = append(backlog, ev)
		}
	}
	if len(backlog) > subBuffer {
		backlog = backlog[len(backlog)-subBuffer:]
	}
	for _, ev := range backlog {
		ch <- ev
	}

	var once sync.Once
	cancel := func() {
		once.Do(func() {
			b.mu.Lock()
			if _, ok := b.subs[id]; ok {
				delete(b.subs, id)
				close(ch)
			}
			b.mu.Unlock()
		})
	}
	return ch, cancel
}

// orderedLocked returns the ring contents oldest first. Callers hold b.mu.
func (b *Bus) orderedLocked() []Event {
	if len(b.events) < b.cap {
		return b.events
	}
	out := make([]Event, 0, len(b.events))
	out = append(out, b.events[b.head:]...)
	out = append(out, b.events[:b.head]...)
	return out
}

func cloneData(data map[string]any) map[string]any {
	if data == nil {
		return nil
	}
	out := make(map[string]any, len(data))
	for k, v := range data {
		out[k] = v
	}
	return out
}
