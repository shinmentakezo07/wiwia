package server

import (
	"net/http"
	"sync"

	"github.com/gin-gonic/gin"

	"wiwi-go/internal/auth"
	"wiwi-go/internal/core"
)

// Python parity (wiwi/server/app.py): playground keys are minted
// automatically for a browser session, so they are TTL-bounded and capped per
// owner. The plaintext never reaches the browser: the wrapper resolves it
// server-side and injects it into the normal chat pipeline.
const (
	playgroundKeyTTLSeconds = int64(24 * 3600)
	maxPlaygroundKeysPerOwn = 5
	pgBearerCacheMax        = 256
)

// pgBearerCache is Python's _PgBearerCache: a bounded LRU of actor id ->
// playground key plaintext. Holding the plaintext here is what lets
// /v1/playground/completions authenticate with only the session cookie; the
// key is re-validated on every resolution so a TTL expiry or a revoke is
// healed by a fresh mint instead of surfacing as a stale-key 401.
type pgBearerCache struct {
	mu      sync.Mutex
	max     int
	entries map[string]string
	order   []string // least-recently used first
}

func newPGBearerCache(max int) *pgBearerCache {
	return &pgBearerCache{max: max, entries: map[string]string{}}
}

func (c *pgBearerCache) get(key string) string {
	c.mu.Lock()
	defer c.mu.Unlock()
	val, ok := c.entries[key]
	if !ok {
		return ""
	}
	c.touch(key)
	return val
}

func (c *pgBearerCache) set(key, plaintext string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.untouch(key)
	c.entries[key] = plaintext
	c.order = append(c.order, key)
	for len(c.entries) > c.max {
		oldest := c.order[0]
		c.order = c.order[1:]
		delete(c.entries, oldest)
	}
}

func (c *pgBearerCache) drop(key string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.untouch(key)
	delete(c.entries, key)
}

func (c *pgBearerCache) touch(key string) {
	c.untouch(key)
	c.order = append(c.order, key)
}

func (c *pgBearerCache) untouch(key string) {
	for i, k := range c.order {
		if k == key {
			c.order = append(c.order[:i], c.order[i+1:]...)
			return
		}
	}
}

// keyOwner mirrors Python's _key_owner_id: every real account owns its key,
// admin included. The synthetic master has no users row, and Authenticate
// fails closed on an owner that does not exist, so its key stays unowned.
func keyOwner(u *auth.User) string {
	if u.ID == auth.MasterUserID {
		return ""
	}
	return u.ID
}

// mintPlaygroundKey mints a fresh playground key for *u* and registers its
// plaintext so the wrapper reuses exactly the key just handed out instead of
// minting a rival on the next call.
func (a *App) mintPlaygroundKey(u *auth.User) (string, *auth.VKey, error) {
	plain, v, err := a.State.Auth.MintPlaygroundKey(
		keyOwner(u), playgroundKeyTTLSeconds, maxPlaygroundKeysPerOwn)
	if err != nil {
		return "", nil, err
	}
	a.State.PGBearers.set(u.ID, plain)
	return plain, v, nil
}

// playgroundKeyFor returns a still-authenticating playground key for *u*,
// reusing the cached plaintext when it is live and minting otherwise.
func (a *App) playgroundKeyFor(u *auth.User) (string, *auth.VKey, error) {
	if plain := a.State.PGBearers.get(u.ID); plain != "" {
		if v, err := a.State.Auth.Authenticate(plain); err == nil {
			return plain, v, nil
		}
		a.State.PGBearers.drop(u.ID)
	}
	return a.mintPlaygroundKey(u)
}

// keyAuthInfo maps a virtual key onto the request context's auth identity,
// the same shape resolveAuth produces for a bearer.
func keyAuthInfo(v *auth.VKey) *core.AuthInfo {
	return &core.AuthInfo{
		KeyID: v.ID, KeyName: v.Name, MaxBudget: derefF(v.MaxBudget), Spend: v.Spend,
		Models: v.Models, RPM: derefI(v.RPM), TPM: derefI(v.TPM),
	}
}

func abortAuth(c *gin.Context, message string) {
	_, body := wireError(core.SurfaceChat, 401, "authentication_error", message)
	c.AbortWithStatusJSON(http.StatusUnauthorized, body)
}

// requirePlayground admits /v1/playground/completions. An explicit bearer
// still works (CLI and back-compat clients); otherwise the wiwi_session cookie
// identifies the caller and the playground virtual key is resolved server-side
// and injected as the auth identity. Python's route behaves identically.
func (a *App) requirePlayground(c *gin.Context) {
	if bearer(c) != "" {
		info, ok := a.resolveAuth(c)
		if !ok {
			abortAuth(c, "invalid or missing API key")
			return
		}
		c.Set("auth", info)
		c.Next()
		return
	}
	cookie, err := c.Cookie("wiwi_session")
	if err != nil || cookie == "" {
		abortAuth(c, "authentication required")
		return
	}
	u, err := a.State.Auth.ValidateSession(a.State.SessionSecret, cookie)
	if err != nil {
		abortAuth(c, "authentication required")
		return
	}
	_, v, err := a.playgroundKeyFor(u)
	if err != nil {
		a.writeErr(c, core.SurfaceChat, 500, "api_error",
			"could not prepare the playground key")
		return
	}
	c.Set("auth", keyAuthInfo(v))
	c.Next()
}
