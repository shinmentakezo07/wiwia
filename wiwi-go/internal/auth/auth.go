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

// VKey is a virtual key as stored (never carries the plaintext). The JSON
// shape is the console's (console_shapes.go maps it to Python's key objects);
// the persistence layer beneath it uses Python's schema columns.
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

// Store exposes the underlying store for callers that need raw SQL over the
// shared schema (the interop helper, admin ad-hoc queries).
func (s *Service) Store() *store.Store { return s.store }

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

// nowFloat is the epoch float Python writes into every timestamp column
// (time.time()). Whole-second int64s stay exact in a float64, so converting
// back with int64(f) is lossless for API-shaped values.
func nowFloat() float64 { return float64(time.Now().UnixNano()) / 1e9 }

// modelsJSON mirrors Python's “models“ column: a JSON-array TEXT, "[]" when
// empty — never a comma-joined string.
func modelsJSON(models []string) string {
	if len(models) == 0 {
		return "[]"
	}
	b, err := json.Marshal(models)
	if err != nil {
		return "[]"
	}
	return string(b)
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
	var ownerArg any
	if owner != "" {
		ownerArg = owner
		if maxKeys > 0 {
			var count int
			_ = s.store.QueryRow(s.store.Rebind(`SELECT COUNT(*) FROM vkeys WHERE owner_id=?`), owner).Scan(&count)
			if count >= maxKeys {
				return "", nil, fmt.Errorf("max_keys_per_user (%d) reached", maxKeys)
			}
		}
	}
	plain := GeneratePlaintext()
	hash := HashKey(plain)
	id := "vk_" + randomToken(8)
	now := time.Now().Unix()
	nowF := nowFloat()
	var expiresF any
	var expires int64
	if ttlSeconds > 0 {
		expires = now + ttlSeconds
		expiresF = float64(expires)
	}
	if _, err := s.store.DB().Exec(s.store.Rebind(
		`INSERT INTO vkeys(id,key_hash,key_alias,models,max_budget,spend_to_date,rpm,tpm,expires_at,disabled,owner_id,created_at,updated_at)
		 VALUES(?,?,?,?,?,0,?,?,?,?,?,?,?)`),
		id, hash, name, modelsJSON(models), maxBudget, rpm, tpm, expiresF, 0, ownerArg, nowF, nowF); err != nil {
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
		`SELECT v.id,v.key_alias,v.key_hash,v.max_budget,v.spend_to_date,v.rpm,v.tpm,v.models,v.disabled,v.expires_at,v.created_at,v.budget_reserved
		 FROM vkeys v
		 WHERE v.key_hash=?
		 AND (v.owner_id IS NULL OR EXISTS (
			 SELECT 1 FROM users u WHERE u.id = v.owner_id AND COALESCE(u.disabled,0) = 0))`), hash)
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
	var disabled int
	var spend, reserved *float64
	// created_at/expires_at are epoch floats in Python's schema. A *int64
	// scan of a float64 value fails on Postgres, so scan float64 and convert.
	var createdF float64
	var expiresF *float64
	if err := r.Scan(&v.ID, &v.Name, &v.KeyHash, &maxBudget, &spend, &rpm, &tpm,
		&models, &disabled, &expiresF, &createdF, &reserved); err != nil {
		return nil, err
	}
	v.MaxBudget = maxBudget
	v.RPM = rpm
	v.TPM = tpm
	v.Enabled = disabled == 0
	if spend != nil {
		v.Spend = *spend
	}
	if reserved != nil {
		v.BudgetReserved = *reserved
	}
	if expiresF != nil {
		v.ExpiresAt = int64(*expiresF)
	}
	v.CreatedAt = int64(createdF)
	v.Models = nil
	if models != "" && models != "[]" {
		_ = json.Unmarshal([]byte(models), &v.Models)
	}
	return &v, nil
}

// List returns all virtual keys (no plaintext).
func (s *Service) List() ([]*VKey, error) {
	rows, err := s.store.Query(s.store.Rebind(
		`SELECT id,key_alias,key_hash,max_budget,spend_to_date,rpm,tpm,models,disabled,expires_at,created_at,budget_reserved
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
		`SELECT id,key_alias,key_hash,max_budget,spend_to_date,rpm,tpm,models,disabled,expires_at,created_at,budget_reserved
		 FROM vkeys WHERE id=?`), id)
	return scanKey(row)
}

func (s *Service) SetEnabled(id string, enabled bool) error {
	_, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET disabled=?, updated_at=? WHERE id=?`),
		boolInt(!enabled), nowFloat(), id)
	return err
}

func (s *Service) Delete(id string) error {
	_, err := s.store.DB().Exec(s.store.Rebind(`DELETE FROM vkeys WHERE id=?`), id)
	return err
}

func (s *Service) Patch(id string, maxBudget *float64, rpm, tpm *int, models []string, name *string) error {
	if name != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET key_alias=?, updated_at=? WHERE id=?`), *name, nowFloat(), id); err != nil {
			return err
		}
	}
	if maxBudget != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET max_budget=?, updated_at=? WHERE id=?`), *maxBudget, nowFloat(), id); err != nil {
			return err
		}
	}
	if rpm != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET rpm=?, updated_at=? WHERE id=?`), *rpm, nowFloat(), id); err != nil {
			return err
		}
	}
	if tpm != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET tpm=?, updated_at=? WHERE id=?`), *tpm, nowFloat(), id); err != nil {
			return err
		}
	}
	if models != nil {
		if _, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET models=?, updated_at=? WHERE id=?`), modelsJSON(models), nowFloat(), id); err != nil {
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
		_, err := s.store.DB().Exec(s.store.Rebind(
			`UPDATE vkeys SET `+col+`=?, updated_at=? WHERE id=?`), v, nowFloat(), id)
		return err
	}
	if raw, ok := fields["name"]; ok && !isJSONNull(raw) {
		var v string
		if json.Unmarshal(raw, &v) == nil {
			if err := exec("key_alias", v); err != nil {
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
			if err := exec(f.col, float64(v)); err != nil {
				return err
			}
		}
	}
	if raw, ok := fields["models"]; ok {
		if isJSONNull(raw) {
			return exec("models", "[]")
		}
		var models []string
		if json.Unmarshal(raw, &models) == nil {
			return exec("models", modelsJSON(models))
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
		`SELECT max_budget,spend_to_date,budget_reserved FROM vkeys WHERE id=?`), id)
	var maxBudget *float64
	var spend, reserved float64
	if err := row.Scan(&maxBudget, &spend, &reserved); err != nil {
		return err
	}
	if maxBudget != nil && *maxBudget > 0 && spend+reserved+dollars > *maxBudget {
		return ErrBudgetExceeded
	}
	if _, err := tx.Exec(s.store.Rebind(
		`UPDATE vkeys SET budget_reserved=?, reserved_at=? WHERE id=?`), reserved+dollars, nowFloat(), id); err != nil {
		return err
	}
	return tx.Commit()
}

// Settle converts a reservation into spend (refunding the unused part).
func (s *Service) Settle(id string, reserved, cost float64) error {
	_, err := s.store.DB().Exec(s.store.Rebind(
		`UPDATE vkeys SET spend_to_date=spend_to_date+?, updated_at=?,`+
			` budget_reserved=CASE WHEN budget_reserved>=? THEN budget_reserved-? ELSE 0 END WHERE id=?`),
		cost, nowFloat(), reserved, reserved, id)
	return err
}

// AddSpend charges a key directly (used when no reservation was made).
func (s *Service) AddSpend(id string, cost float64) error {
	if id == "" || cost <= 0 {
		return nil
	}
	_, err := s.store.DB().Exec(s.store.Rebind(`UPDATE vkeys SET spend_to_date=spend_to_date+?, updated_at=? WHERE id=?`),
		cost, nowFloat(), id)
	return err
}

// Audit records an admin mutation. id is auto-generated by the database
// (INTEGER autoincrement / SERIAL); Go inserts only the data columns, matching
// Python's journal sink.
func (s *Service) Audit(actor, action, target string, diff string) {
	_, _ = s.store.DB().Exec(s.store.Rebind(
		`INSERT INTO audit_logs(ts,actor,action,target,diff) VALUES(?,?,?,?,?)`),
		nowFloat(), actor, action, target, diff)
}

func boolInt(b bool) int {
	if b {
		return 1
	}
	return 0
}

var _ = fmt.Sprintf
