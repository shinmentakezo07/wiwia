// Package cache holds the optional response cache and the singleflight
// de-duplicator used to collapse concurrent identical upstream calls.
package cache

import (
	"container/list"
	"sync"
	"time"
)

// defaultMaxEntries is used when the caller passes a non-positive cap: a cache
// with no capacity is never what a caller means.
const defaultMaxEntries = 1024

type entry struct {
	key     string
	val     []byte
	expires time.Time // zero means it never expires
	elem    *list.Element
}

// Cache is a byte cache with a TTL and LRU eviction. It is safe for concurrent
// use and copies values on both Set and Get, so callers never share a buffer
// with the cache.
type Cache struct {
	enabled bool // immutable
	ttl     time.Duration
	max     int

	mu      sync.Mutex
	entries map[string]*entry
	lru     *list.List // front is most recently used
}

// New builds a cache. enabled=false makes Get always miss and Set a no-op.
// ttlS <= 0 means entries never expire (they are still evicted at the cap);
// maxEntries <= 0 falls back to defaultMaxEntries.
func New(enabled bool, ttlS float64, maxEntries int) *Cache {
	if maxEntries <= 0 {
		maxEntries = defaultMaxEntries
	}
	var ttl time.Duration
	if ttlS > 0 {
		ttl = time.Duration(ttlS * float64(time.Second))
	}
	return &Cache{
		enabled: enabled,
		ttl:     ttl,
		max:     maxEntries,
		entries: make(map[string]*entry, maxEntries),
		lru:     list.New(),
	}
}

// Enabled reports whether the cache stores anything.
func (c *Cache) Enabled() bool { return c.enabled }

// Get returns a copy of the cached value, or ok=false on a miss or expiry.
func (c *Cache) Get(key string) ([]byte, bool) {
	if !c.enabled {
		return nil, false
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	e, ok := c.entries[key]
	if !ok {
		return nil, false
	}
	if !e.expires.IsZero() && time.Now().After(e.expires) {
		c.lru.Remove(e.elem)
		delete(c.entries, key)
		return nil, false
	}
	c.lru.MoveToFront(e.elem)
	return cloneBytes(e.val), true
}

// Set stores a copy of val, refreshing the entry's TTL and LRU position and
// evicting the least recently used entries beyond the cap.
func (c *Cache) Set(key string, val []byte) {
	if !c.enabled || c.max <= 0 {
		return
	}
	var expires time.Time
	if c.ttl > 0 {
		expires = time.Now().Add(c.ttl)
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if e, ok := c.entries[key]; ok {
		e.val = cloneBytes(val)
		e.expires = expires
		c.lru.MoveToFront(e.elem)
		return
	}
	e := &entry{key: key, val: cloneBytes(val), expires: expires}
	e.elem = c.lru.PushFront(e)
	c.entries[key] = e
	for len(c.entries) > c.max {
		back := c.lru.Back()
		if back == nil {
			break
		}
		evicted := back.Value.(*entry)
		c.lru.Remove(back)
		delete(c.entries, evicted.key)
	}
}

func cloneBytes(b []byte) []byte {
	if b == nil {
		return nil
	}
	out := make([]byte, len(b))
	copy(out, b)
	return out
}
