package server

import (
	"encoding/json"
	"time"

	"wiwi-go/internal/store"
)

// ResponseStore persists Responses-API state behind `store` +
// `previous_response_id`. Ids are key-scoped and expiry is enforced on read.
type ResponseStore struct {
	store   *store.Store
	enabled bool
	ttl     float64
}

func NewResponseStore(s *store.Store, enabled bool, ttlS float64) *ResponseStore {
	if ttlS <= 0 {
		ttlS = 86400
	}
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
		`INSERT INTO responses(id,key_id,body,created_at,expires_at) VALUES(?,?,?,?,?)`),
		id, keyID, string(b), now, expires)
	return err
}

func (r *ResponseStore) Get(id, keyID string) (map[string]any, bool) {
	if !r.Enabled() {
		return nil, false
	}
	row := r.store.QueryRow(r.store.Rebind(
		`SELECT body,expires_at FROM responses WHERE id=? AND key_id=?`), id, keyID)
	var body string
	var expires int64
	if err := row.Scan(&body, &expires); err != nil {
		return nil, false
	}
	if expires > 0 && time.Now().Unix() > expires {
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
	res, err := r.store.DB().Exec(r.store.Rebind(
		`DELETE FROM responses WHERE id=? AND key_id=?`), id, keyID)
	if err != nil {
		return false
	}
	n, _ := res.RowsAffected()
	return n > 0
}
