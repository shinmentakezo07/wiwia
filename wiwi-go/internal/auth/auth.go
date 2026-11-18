package auth

import (
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"wiwi-go/internal/store"
)

var (
	ErrUnauthorized   = errors.New("invalid or missing key")
	ErrBudgetExceeded = errors.New("budget exceeded")
)

// VKey is a virtual key as stored (never carries the plaintext).
type VKey struct {
	ID             string   `json:"id"`
	Name           string   `json:"name"`
	KeyHash        string   `json:"-"`
	MaxBudget      *float64 `json:"max_budget,omitempty"`
	Spend          float64  `json:"spend"`
	RPM            *int     `json:"rpm,omitempty"`
	TPM            *int     `json:"tpm,omitempty"`
	Models         []string `json:"models,omitempty"`
	Enabled        bool     `json:"enabled"`
	ExpiresAt      int64    `json:"expires_at,omitempty"`
	CreatedAt      int64    `json:"created_at"`
	BudgetReserved float64  `json:"-"`
}

// Service owns virtual-key, user, and session persistence.
type Service struct {
	store *store.Store
}

func NewService(s *store.Store) *Service { return &Service{store: s} }

func HashKey(plaintext string) string {
	sum := sha256.Sum256([]byte(plaintext))
	return hex.EncodeToString(sum[:])
}

func randomToken(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func GeneratePlaintext() string {
	return "sk-wiwi-" + randomToken(24)
}

// Generate mints a new virtual key, returning the plaintext exactly once.
func (s *Service) Generate(name string, maxBudget *float64, rpm, tpm *int, models []string, ttlSeconds int64) (string, *VKey, error) {
	return s.GenerateOwned("", name, maxBudget, rpm, tpm, models, ttlSeconds, 0)
}

// GenerateOwned mints a key owned by *owner* and enforces max_keys_per_user.
func (s *Service) GenerateOwned(owner, name string, maxBudget *float64, rpm, tpm *int, models []string, ttlSeconds int64, maxKeys int) (string, *VKey, error) {
	if name == "" {
		name = "key"
	}
	if owner != "" && maxKeys > 0 {
		var count int
		_ = s.store.QueryRow(s.store.Rebind(`SELECT COUNT(*) FROM vkeys WHERE owner=?`), owner).Scan(&count)
		if count >= maxKeys {
			return "", nil, fmt.Errorf("max_keys_per_user (%d) reached", maxKeys)
		}
	}
	plain := GeneratePlaintext()
	hash := HashKey(plain)
	id := "vk_" + randomToken(8)
	now := time.Now().Unix()
	var expires int64
	if ttlSeconds > 0 {
		expires = now + ttlSeconds
	}
	modelsStr := strings.Join(models, ",")
	_, err := s.store.DB().Exec(s.store.Rebind(
		`INSERT INTO vkeys(id,name,owner,key_hash,max_budget,spend,rpm,tpm,models,enabled,expires_at,created_at,budget_reserved)
		 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)`),
		id, name, owner, hash, maxBudget, 0.0, rpm, tpm, modelsStr, 1, expires, now, 0.0)
	if err != nil {
		return "", nil, err
	}
	return plain, &VKey{
		ID: id, Name: name, KeyHash: hash, MaxBudget: maxBudget, RPM: rpm, TPM: tpm,
		Models: models, Enabled: true, ExpiresAt: expires, CreatedAt: now,
	}, nil
}

// Authenticate resolves a plaintext key to its record.
func (s *Service) Authenticate(plaintext string) (*VKey, error) {
	if plaintext == "" {
		return nil, ErrUnauthorized
	}
	hash := HashKey(plaintext)
	row := s.store.QueryRow(s.store.Rebind(
		`SELECT id,name,key_hash,max_budget,spend,rpm,tpm,models,enabled,expires_at,created_at,budget_reserved
		 FROM vkeys WHERE key_hash=?`), hash)
	v, err := scanKey(row)
	if err != nil {
		return nil, ErrUnauthorized
	}
	// constant-time compare of the hash
	if subtle.ConstantTimeCompare([]byte(v.KeyHash), []byte(hash)) != 1 {
		return nil, ErrUnauthorized
	}
	if !v.Enabled {
		return nil, ErrUnauthorized
	}
	if v.ExpiresAt > 0 && time.Now().Unix() > v.ExpiresAt {
		return nil, ErrUnauthorized
	}
	return v, nil
}

type rowScanner interface{ Scan(dest ...any) error }

func scanKey(r rowScanner) (*VKey, error) {
	var v VKey
	var maxBudget *float64
	var rpm, tpm *int
	var models string
	var enabled int
	var spend, reserved *float64
	var expires *int64
	if err := r.Scan(&v.ID, &v.Name, &v.KeyHash, &maxBudget, &spend, &rpm, &tpm,
		&models, &enabled, &expires, &v.CreatedAt, &reserved); err != nil {
		return nil, err
	}
	v.MaxBudget = maxBudget
	v.RPM = rpm
	v.TPM = tpm
	v.Enabled = enabled != 0
	if spend != nil {
		v.Spend = *spend
	}
	if reserved != nil {
		v.BudgetReserved = *reserved
	}
	if expires != nil {
		v.ExpiresAt = *expires
	}
	if models != "" {
		v.Models = strings.Split(models, ",")
	}
	return &v, nil
}

// List returns all virtual keys (no plaintext).
func (s *Service) List() ([]*VKey, error) {
	rows, err := s.store.Query(s.store.Rebind(
		`SELECT id,name,key_hash,max_budget,spend,rpm,tpm,models,enabled,expires_at,created_at,budget_reserved
		 FROM vkeys ORDER BY created_at DESC`))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*VKey
	for rows.Next() {
		v, err := scanKey(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, v)
	}
	return out, rows.Err()
}

func (s *Service) Get(id string) (*VKey, error) {
	row := s.store.QueryRow(s.store.Rebind(
		`SELECT id,name,key_hash,max_budget,spend,rpm,tpm,models,enabled,expires_at,created_at,budget_reserved
		 FROM vkeys WHERE id=?`), id)
	return scanKey(row)
}

func (s *Service) SetEnabled(id string, enabled bool) error {
	_, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET enabled=? WHERE id=?`), boolInt(enabled), id)
	return err
}

func (s *Service) Delete(id string) error {
	_, err := s.store.DB().Exec(s.store.Rebind(`DELETE FROM vkeys WHERE id=?`), id)
	return err
}

func (s *Service) Patch(id string, maxBudget *float64, rpm, tpm *int, models []string, name *string) error {
	if name != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET name=? WHERE id=?`), *name, id); err != nil {
			return err
		}
	}
	if maxBudget != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET max_budget=? WHERE id=?`), *maxBudget, id); err != nil {
			return err
		}
	}
	if rpm != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET rpm=? WHERE id=?`), *rpm, id); err != nil {
			return err
		}
	}
	if tpm != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET tpm=? WHERE id=?`), *tpm, id); err != nil {
			return err
		}
	}
	if models != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET models=? WHERE id=?`), strings.Join(models, ","), id); err != nil {
			return err
		}
	}
	return nil
}

// PatchConsole applies the console's PATCH body. Unlike Patch, it distinguishes
// an absent key (leave unchanged) from an explicit JSON null (clear the
// column), which is how the console's "tick to clear" controls work.
func (s *Service) PatchConsole(id string, fields map[string]json.RawMessage) error {
	exec := func(col string, v any) error {
		_, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET `+col+`=? WHERE id=?`), v, id)
		return err
	}
	if raw, ok := fields["name"]; ok && !isJSONNull(raw) {
		var v string
		if json.Unmarshal(raw, &v) == nil {
			if err := exec("name", v); err != nil {
				return err
			}
		}
	}
	if raw, ok := fields["max_budget"]; ok {
		if isJSONNull(raw) {
			if err := exec("max_budget", nil); err != nil {
				return err
			}
		} else {
			var v float64
			if json.Unmarshal(raw, &v) == nil {
				if err := exec("max_budget", v); err != nil {
					return err
				}
			}
		}
	}
	for _, f := range []struct {
		key string
		col string
	}{{"rpm", "rpm"}, {"tpm", "tpm"}, {"expires_at", "expires_at"}} {
		raw, ok := fields[f.key]
		if !ok {
			continue
		}
		if isJSONNull(raw) {
			if err := exec(f.col, nil); err != nil {
				return err
			}
			continue
		}
		var v int64
		if json.Unmarshal(raw, &v) == nil {
			if err := exec(f.col, v); err != nil {
				return err
			}
		}
	}
	if raw, ok := fields["models"]; ok {
		if isJSONNull(raw) {
			return exec("models", "")
		}
		var models []string
		if json.Unmarshal(raw, &models) == nil {
			return exec("models", strings.Join(models, ","))
		}
	}
	return nil
}

func isJSONNull(raw json.RawMessage) bool {
	return strings.TrimSpace(string(raw)) == "null"
}

// ReserveBudget atomically reserves dollars against a key's max_budget.
// Returns ErrBudgetExceeded when the reservation would exceed the cap.
func (s *Service) ReserveBudget(id string, dollars float64) error {
	if dollars <= 0 {
		return nil
	}
	tx, err := s.store.DB().Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row := tx.QueryRow(s.store.Rebind(
		`SELECT max_budget,spend,budget_reserved FROM vkeys WHERE id=?`), id)
	var maxBudget *float64
	var spend, reserved float64
	if err := row.Scan(&maxBudget, &spend, &reserved); err != nil {
		return err
	}
	if maxBudget != nil && *maxBudget > 0 && spend+reserved+dollars > *maxBudget {
		return ErrBudgetExceeded
	}
	if _, err := tx.Exec(s.store.Rebind(
		`UPDATE vkeys SET budget_reserved=?, reserved_at=? WHERE id=?`), reserved+dollars, time.Now().Unix(), id); err != nil {
		return err
	}
	return tx.Commit()
}

// Settle converts a reservation into spend (refunding the unused part).
func (s *Service) Settle(id string, reserved, cost float64) error {
	_, err := s.store.DB().Exec(s.store.Rebind(
		`UPDATE vkeys SET spend=spend+?, budget_reserved=CASE WHEN budget_reserved>=? THEN budget_reserved-? ELSE 0 END WHERE id=?`),
		cost, reserved, reserved, id)
	return err
}

// AddSpend charges a key directly (used when no reservation was made).
func (s *Service) AddSpend(id string, cost float64) error {
	if id == "" || cost <= 0 {
		return nil
	}
	_, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET spend=spend+? WHERE id=?`), cost, id)
	return err
}

// Audit records an admin mutation.
func (s *Service) Audit(actor, action, target string, diff string) {
	id := "aud_" + randomToken(8)
	_, _ = s.store.DB().Exec(s.store.Rebind(
		`INSERT INTO audit_logs(id,ts,actor,action,target,diff) VALUES(?,?,?,?,?,?)`),
		id, time.Now().Unix(), actor, action, target, diff)
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

var _ = fmt.Sprintf
