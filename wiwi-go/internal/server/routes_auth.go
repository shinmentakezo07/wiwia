package server

import (
	"crypto/subtle"
	"net/http"

	"github.com/gin-gonic/gin"

	"wiwi-go/internal/auth"
)

func (a *App) handleSignup(c *gin.Context) {
	var body struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := c.ShouldBindJSON(&body); err != nil || body.Username == "" || body.Password == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "username and password required"})
		return
	}
	role := "user"
	if users, _ := a.State.Auth.ListUsers(); len(users) == 0 {
		role = "admin" // first account is the admin
	}
	u, err := a.State.Auth.CreateUser(body.Username, body.Password, role)
	if err != nil {
		c.JSON(http.StatusConflict, gin.H{"error": err.Error()})
		return
	}
	sess, err := a.State.Auth.NewSession(a.State.SessionSecret, u.ID, 86400)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.SetCookie("wiwi_session", sess, 86400, "/", "", false, true)
	c.JSON(http.StatusOK, gin.H{"user": u})
}

func (a *App) handleLogin(c *gin.Context) {
	var body struct {
		Username  string `json:"username"`
		Password  string `json:"password"`
		MasterKey string `json:"master_key"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body"})
		return
	}
	// Master-key login (the console's "Master key" tab posts {master_key}).
	// Compare in constant time, then mint a synthetic master admin session —
	// mirroring the Python /auth/login master branch. Without this the Go
	// backend rejected every master-key login with a 401.
	if body.MasterKey != "" {
		mk := a.State.Cfg.General.MasterKey
		if mk == "" || subtle.ConstantTimeCompare([]byte(body.MasterKey), []byte(mk)) != 1 {
			c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid master key"})
			return
		}
		sess, err := a.State.Auth.NewMasterSession(a.State.SessionSecret, 86400)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
			return
		}
		c.SetCookie("wiwi_session", sess, 86400, "/", "", false, true)
		c.JSON(http.StatusOK, gin.H{"user": auth.MasterUser()})
		return
	}
	u, err := a.State.Auth.AuthenticateUser(body.Username, body.Password)
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid username or password"})
		return
	}
	sess, err := a.State.Auth.NewSession(a.State.SessionSecret, u.ID, 86400)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.SetCookie("wiwi_session", sess, 86400, "/", "", false, true)
	c.JSON(http.StatusOK, gin.H{"user": u})
}

func (a *App) handleLogout(c *gin.Context) {
	if cookie, err := c.Cookie("wiwi_session"); err == nil {
		a.State.Auth.DeleteSession(a.State.SessionSecret, cookie)
	}
	c.SetCookie("wiwi_session", "", -1, "/", "", false, true)
	c.JSON(http.StatusOK, gin.H{"ok": true})
}

func (a *App) handleMe(c *gin.Context) {
	// Master bearer (back-compat): the console stores the master key and sends
	// it as `Authorization: Bearer ...` on every /auth/me call. Resolve it the
	// same way Python's resolve_current_user does, so a master login stays
	// signed in even when the cookie is absent or stale.
	if key := bearer(c); key != "" {
		if mk := a.State.Cfg.General.MasterKey; mk != "" &&
			subtle.ConstantTimeCompare([]byte(key), []byte(mk)) == 1 {
			c.JSON(http.StatusOK, gin.H{"user": auth.MasterUser()})
			return
		}
	}
	cookie, err := c.Cookie("wiwi_session")
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "not logged in"})
		return
	}
	u, err := a.State.Auth.ValidateSession(a.State.SessionSecret, cookie)
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "not logged in"})
		return
	}
	c.JSON(http.StatusOK, gin.H{"user": u})
}

func (a *App) handlePlaygroundKey(c *gin.Context) {
	cookie, err := c.Cookie("wiwi_session")
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "not logged in"})
		return
	}
	u, err := a.State.Auth.ValidateSession(a.State.SessionSecret, cookie)
	if err != nil {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "not logged in"})
		return
	}
	plain, v, err := a.State.Auth.Generate("playground-"+u.Username, nil, nil, nil, nil, 3600)
	if err != nil {
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}
	c.JSON(http.StatusOK, gin.H{"key": plain, "id": v.ID})
}
