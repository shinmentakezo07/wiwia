package auth

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"strings"
	"time"
)

type User struct {
	ID        string `json:"id"`
	Username  string `json:"username"`
	Role      string `json:"role"`
	CreatedAt int64  `json:"created_at"`
	Disabled  bool   `json:"disabled"`
}

var (
	ErrUserExists = errors.New("user already exists")
	ErrBadLogin   = errors.New("invalid username or password")
	ErrBadSession = errors.New("invalid session")
)

// MasterUserID is the reserved id of the synthetic master admin. It has no row
// in the users table (Python's SYNTHETIC_MASTER_ID); ValidateSession recognises
// it by this id instead of joining, so the master never appears in user lists.
const MasterUserID = "master"

// MasterUser is the synthetic admin actor minted by a master-key login.
func MasterUser() *User {
	return &User{ID: MasterUserID, Username: MasterUserID, Role: "admin"}
}

// NewMasterSession issues a signed session cookie for the synthetic master
// admin. It must only be called after the caller has verified the master key.
func (s *Service) NewMasterSession(secret string, ttlSeconds int64) (string, error) {
	return s.NewSession(secret, MasterUserID, ttlSeconds)
}

// pbkdf2SHA256 implements PBKDF2-HMAC-SHA256 with a single 32-byte block.
func pbkdf2SHA256(password, salt []byte, iter, keyLen int) []byte {
	h := hmac.New(sha256.New, password)
	out := make([]byte, 0, keyLen)
	block := uint32(1)
	for len(out) < keyLen {
		h.Reset()
		h.Write(salt)
		var idx [4]byte
		binary.BigEndian.PutUint32(idx[:], block)
		h.Write(idx[:])
		u := h.Sum(nil)
		t := make([]byte, len(u))
		copy(t, u)
		for i := 1; i < iter; i++ {
			h.Reset()
			h.Write(u)
			u = h.Sum(nil)
			for j := range t {
				t[j] ^= u[j]
			}
		}
		out = append(out, t...)
		block++
	}
	return out[:keyLen]
}

func hashPassword(password string) string {
	salt := make([]byte, 16)
	_, _ = rand.Read(salt)
	dk := pbkdf2SHA256([]byte(password), salt, 100_000, 32)
	return "pbkdf2$100000$" + base64.RawStdEncoding.EncodeToString(salt) + "$" + base64.RawStdEncoding.EncodeToString(dk)
}

func verifyPassword(password, stored string) bool {
	parts := strings.Split(stored, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2" {
		return false
	}
	var iter int
	if _, err := fmtSscan(parts[1], &iter); err != nil {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[2])
	if err != nil {
		return false
	}
	want, err := base64.RawStdEncoding.DecodeString(parts[3])
	if err != nil {
		return false
	}
	got := pbkdf2SHA256([]byte(password), salt, iter, len(want))
	return subtle.ConstantTimeCompare(got, want) == 1
}

func fmtSscan(s string, out *int) (int, error) {
	n := 0
	if s == "" {
		return 0, errors.New("empty")
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return 0, errors.New("bad int")
		}
		n = n*10 + int(r-'0')
	}
	*out = n
	return 1, nil
}

// CreateUser registers an account.
func (s *Service) CreateUser(username, password, role string) (*User, error) {
	if role == "" {
		role = "user"
	}
	id := "usr_" + randomToken(8)
	now := time.Now().Unix()
	_, err := s.store.DB().Exec(s.store.Rebind(
		`INSERT INTO users(id,username,pw_hash,role,created_at) VALUES(?,?,?,?,?)`),
		id, username, hashPassword(password), role, now)
	if err != nil {
		return nil, ErrUserExists
	}
	return &User{ID: id, Username: username, Role: role, CreatedAt: now}, nil
}

// AuthenticateUser verifies a username/password pair.
func (s *Service) AuthenticateUser(username, password string) (*User, error) {
	row := s.store.QueryRow(s.store.Rebind(
		`SELECT id,username,pw_hash,role,created_at,COALESCE(disabled,0) FROM users WHERE username=?`), username)
	var u User
	var pwHash string
	var disabled int
	if err := row.Scan(&u.ID, &u.Username, &pwHash, &u.Role, &u.CreatedAt, &disabled); err != nil {
		return nil, ErrBadLogin
	}
	if disabled != 0 {
		return nil, ErrBadLogin
	}
	if !verifyPassword(password, pwHash) {
		return nil, ErrBadLogin
	}
	return &u, nil
}

func (s *Service) ListUsers() ([]*User, error) {
	rows, err := s.store.Query(s.store.Rebind(
		`SELECT id,username,role,created_at,COALESCE(disabled,0) FROM users ORDER BY created_at`))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var out []*User
	for rows.Next() {
		var u User
		var disabled int
		if err := rows.Scan(&u.ID, &u.Username, &u.Role, &u.CreatedAt, &disabled); err != nil {
			return nil, err
		}
		u.Disabled = disabled != 0
		out = append(out, &u)
	}
	return out, rows.Err()
}

func (s *Service) GetUser(id string) (*User, error) {
	row := s.store.QueryRow(s.store.Rebind(
		`SELECT id,username,role,created_at,COALESCE(disabled,0) FROM users WHERE id=?`), id)
	var u User
	var disabled int
	if err := row.Scan(&u.ID, &u.Username, &u.Role, &u.CreatedAt, &disabled); err != nil {
		return nil, err
	}
	u.Disabled = disabled != 0
	return &u, nil
}

func (s *Service) SetUserRole(id, role string) error {
	_, err := s.store.DB().Exec(s.store.Rebind(`UPDATE users SET role=? WHERE id=?`), role, id)
	return err
}

func (s *Service) SetUserDisabled(id string, disabled bool) error {
	v := 0
	if disabled {
		v = 1
	}
	_, err := s.store.DB().Exec(s.store.Rebind(`UPDATE users SET disabled=? WHERE id=?`), v, id)
	return err
}

// NewSession issues an HMAC-signed session id cookie value.
func (s *Service) NewSession(secret, userID string, ttlSeconds int64) (string, error) {
	if ttlSeconds <= 0 {
		ttlSeconds = 86400
	}
	id := randomToken(16)
	now := time.Now().Unix()
	if _, err := s.store.DB().Exec(s.store.Rebind(
		`INSERT INTO sessions(id,user_id,created_at,expires_at) VALUES(?,?,?,?)`),
		id, userID, now, now+ttlSeconds); err != nil {
		return "", err
	}
	return signSession(secret, id), nil
}

// ValidateSession verifies a signed cookie and returns the user.
func (s *Service) ValidateSession(secret, cookie string) (*User, error) {
	id, ok := verifySession(secret, cookie)
	if !ok {
		return nil, ErrBadSession
	}
	// A session can name the synthetic master admin, which has no users row, so
	// read the session's user_id first and special-case the reserved id before
	// joining users (Python's resolve_current_user does the same).
	var userID string
	var expires int64
	if err := s.store.QueryRow(s.store.Rebind(
		`SELECT user_id, expires_at FROM sessions WHERE id=?`), id).
		Scan(&userID, &expires); err != nil {
		return nil, ErrBadSession
	}
	if expires > 0 && time.Now().Unix() > expires {
		return nil, ErrBadSession
	}
	if userID == MasterUserID {
		return MasterUser(), nil
	}
	row := s.store.QueryRow(s.store.Rebind(
		`SELECT id,username,role,created_at,COALESCE(disabled,0) FROM users WHERE id=?`), userID)
	var u User
	var disabled int
	if err := row.Scan(&u.ID, &u.Username, &u.Role, &u.CreatedAt, &disabled); err != nil {
		return nil, ErrBadSession
	}
	if disabled != 0 {
		return nil, ErrBadSession
	}
	return &u, nil
}

func (s *Service) DeleteSession(secret, cookie string) {
	if id, ok := verifySession(secret, cookie); ok {
		_, _ = s.store.DB().Exec(s.store.Rebind(`DELETE FROM sessions WHERE id=?`), id)
	}
}

func signSession(secret, id string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(id))
	return id + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func verifySession(secret, cookie string) (string, bool) {
	i := strings.LastIndex(cookie, ".")
	if i <= 0 {
		return "", false
	}
	id := cookie[:i]
	sig := cookie[i+1:]
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(id))
	want := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	if subtle.ConstantTimeCompare([]byte(sig), []byte(want)) != 1 {
		return "", false
	}
	return id, true
}
