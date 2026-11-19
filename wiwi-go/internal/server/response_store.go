package server

import (
	"encoding/json"
	"sync"
	"time"

	"wiwi-go/internal/store"
)

// ResponseStore persists Responses-API state behind `store` +
// `previous_response_id`. Ids are key-scoped and expiry is enforced on read.
type ResponseStore struct {
	store   *store.Store
	enabled bool
	ttl     float64

	mu              sync.Mutex
	pending         []int64
	nextMaintenance int64
	stop            chan struct{}
	stopOnce        sync.Once
}

func NewResponseStore(s *store.Store, enabled bool, ttlS float64) *ResponseStore {
	// ttl <= 0 means keep forever, matching Python's ResponseStore.
	return &ResponseStore{store: s, enabled: enabled, ttl: ttlS}
}

func (r *ResponseStore) Enabled() bool { return r != nil && r.enabled }

func (r *ResponseStore) Save(id, keyID string, body map[string]any) error {
	if !r.Enabled() {
		return nil
	}
	b, err := json.Marshal(body)
	if err != nil {
		return err
	}
	now := time.Now().Unix()
	expires := int64(0)
	if r.ttl > 0 {
		expires = now + int64(r.ttl)
	}
	_, err = r.store.DB().Exec(r.store.Rebind(
		`INSERT INTO responses(id,key_id,body,created_at,expires_at) VALUES(?,?,?,?,?)
		 ON CONFLICT(id) DO UPDATE SET key_id=excluded.key_id, body=excluded.body,
		 created_at=excluded.created_at, expires_at=excluded.expires_at`),
		id, keyID, string(b), now, expires)
	if err != nil {
		return err
	}
	if r.ttl > 0 {
		r.mu.Lock()
		r.pending = append(r.pending, now)
		r.mu.Unlock()
	}
	return nil
}

func (r *ResponseStore) Get(id, keyID string) (map[string]any, bool) {
	if !r.Enabled() {
		return nil, false
	}
	row := r.store.QueryRow(r.store.Rebind(
		`SELECT body,key_id,created_at FROM responses WHERE id=?`), id)
	var body, storedKey string
	var created int64
	if err := row.Scan(&body, &storedKey, &created); err != nil {
		return nil, false
	}
	if r.ttl > 0 && float64(time.Now().Unix()-created) > r.ttl {
		return nil, false
	}
	if keyID != "master" && storedKey != keyID {
		return nil, false
	}
	var out map[string]any
	if json.Unmarshal([]byte(body), &out) != nil {
		return nil, false
	}
	return out, true
}

func (r *ResponseStore) Delete(id, keyID string) bool {
	if !r.Enabled() {
		return false
	}
	var res interface{ RowsAffected() (int64, error) }
	var err error
	if keyID == "master" {
		res, err = r.store.DB().Exec(r.store.Rebind(`DELETE FROM responses WHERE id=?`), id)
	} else {
		res, err = r.store.DB().Exec(r.store.Rebind(
			`DELETE FROM responses WHERE id=? AND key_id=?`), id, keyID)
	}
	if err != nil {
		return false
	}
	n, _ := res.RowsAffected()
	return n > 0
}

// Sweep deletes rows older than the TTL. It runs at most once per TTL on an
// idle store, and when the oldest observed write could have expired. A plain
// dirty flag cannot represent "written but not yet expired" (round 209).
func (r *ResponseStore) Sweep(nowUnix int64) int64 {
	if !r.Enabled() || r.ttl <= 0 {
		return 0
	}
	r.mu.Lock()
	due := nowUnix >= r.nextMaintenance
	if !due && len(r.pending) > 0 {
		due = float64(nowUnix) >= float64(r.pending[0])+r.ttl
	}
	if !due {
		r.mu.Unlock()
		return 0
	}
	cutoff := float64(nowUnix) - r.ttl
	r.mu.Unlock()

	res, err := r.store.DB().Exec(r.store.Rebind(
		`DELETE FROM responses WHERE created_at < ?`), int64(cutoff))
	var removed int64
	if err == nil {
		removed, _ = res.RowsAffected()
	}

	r.mu.Lock()
	kept := r.pending[:0]
	for _, ts := range r.pending {
		if float64(ts) >= cutoff {
			kept = append(kept, ts)
		}
	}
	r.pending = kept
	r.nextMaintenance = nowUnix + int64(r.ttl)
	r.mu.Unlock()
	return removed
}

// Start launches the background sweeper. intervalS <= 0 disables it.
func (r *ResponseStore) Start(intervalS float64) {
	if r == nil || intervalS <= 0 {
		return
	}
	r.mu.Lock()
	if r.stop != nil {
		r.mu.Unlock()
		return
	}
	r.stop = make(chan struct{})
	stop := r.stop
	r.mu.Unlock()
	go func() {
		ticker := time.NewTicker(time.Duration(intervalS * float64(time.Second)))
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				r.Sweep(time.Now().Unix())
			case <-stop:
				return
			}
		}
	}()
}

// Stop halts the background sweeper (idempotent).
func (r *ResponseStore) Stop() {
	if r == nil {
		return
	}
	r.stopOnce.Do(func() {
		r.mu.Lock()
		stop := r.stop
		r.stop = nil
		r.mu.Unlock()
		if stop != nil {
			close(stop)
		}
	})
}
