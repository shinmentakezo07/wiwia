package server

import (
	"encoding/json"
	"sync"
	"time"

	"wiwi-go/internal/store"
)

// ResponseStore persists Responses-API state behind `store` +
// `previous_response_id`. It writes Python's stored_responses table (same
// columns and float epochs) so the two backends can share one database.
// Ids are key-scoped and expiry is enforced on read.
type ResponseStore struct {
	store   *store.Store
	enabled bool
	ttl     float64

	mu              sync.Mutex
	pending         []float64
	nextMaintenance float64
	stop            chan struct{}
	stopOnce        sync.Once
}

func NewResponseStore(s *store.Store, enabled bool, ttlS float64) *ResponseStore {
	// ttl <= 0 means keep forever, matching Python's ResponseStore.
	return &ResponseStore{store: s, enabled: enabled, ttl: ttlS}
}

func (r *ResponseStore) Enabled() bool { return r != nil && r.enabled }

// Save writes id/body scoped to keyID. body carries the served Responses
// payload plus the request's normalized `_input` items; the table keeps them
// in Python's input_json/output_json split, so a Python worker can read the
// row and vice versa.
func (r *ResponseStore) Save(id, keyID, modelGroup string, body map[string]any) error {
	if !r.Enabled() {
		return nil
	}
	input := body["_input"]
	if input == nil {
		input = []any{}
	}
	inputJSON, err := json.Marshal(input)
	if err != nil {
		return err
	}
	output := cloneMap(body)
	delete(output, "_input")
	outputJSON, err := json.Marshal(output)
	if err != nil {
		return err
	}
	now := float64(time.Now().UnixNano()) / 1e9
	_, err = r.store.DB().Exec(r.store.Rebind(
		`INSERT INTO stored_responses(id,created_at,key_id,surface,model_group,input_json,output_json)
		 VALUES(?,?,?,'responses',?,?,?)
		 ON CONFLICT(id) DO UPDATE SET created_at=excluded.created_at,
		 key_id=excluded.key_id, model_group=excluded.model_group,
		 input_json=excluded.input_json, output_json=excluded.output_json`),
		id, now, keyID, modelGroup, string(inputJSON), string(outputJSON))
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
		`SELECT created_at,key_id,input_json,output_json FROM stored_responses WHERE id=?`), id)
	var created float64
	var storedKey, inputJSON, outputJSON string
	if err := row.Scan(&created, &storedKey, &inputJSON, &outputJSON); err != nil {
		return nil, false
	}
	if r.ttl > 0 && float64(time.Now().UnixNano())/1e9-created > r.ttl {
		return nil, false
	}
	if keyID != "master" && storedKey != keyID {
		return nil, false
	}
	var out map[string]any
	if json.Unmarshal([]byte(outputJSON), &out) != nil {
		return nil, false
	}
	var input any
	if json.Unmarshal([]byte(inputJSON), &input) == nil {
		out["_input"] = input
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
		res, err = r.store.DB().Exec(r.store.Rebind(`DELETE FROM stored_responses WHERE id=?`), id)
	} else {
		res, err = r.store.DB().Exec(r.store.Rebind(
			`DELETE FROM stored_responses WHERE id=? AND key_id=?`), id, keyID)
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
	now := float64(nowUnix)
	r.mu.Lock()
	due := now >= r.nextMaintenance
	if !due && len(r.pending) > 0 {
		due = now >= r.pending[0]+r.ttl
	}
	if !due {
		r.mu.Unlock()
		return 0
	}
	cutoff := now - r.ttl
	r.mu.Unlock()

	res, err := r.store.DB().Exec(r.store.Rebind(
		`DELETE FROM stored_responses WHERE created_at < ?`), cutoff)
	var removed int64
	if err == nil {
		removed, _ = res.RowsAffected()
	}

	r.mu.Lock()
	kept := r.pending[:0]
	for _, ts := range r.pending {
		if ts >= cutoff {
			kept = append(kept, ts)
		}
	}
	r.pending = kept
	r.nextMaintenance = now + r.ttl
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
