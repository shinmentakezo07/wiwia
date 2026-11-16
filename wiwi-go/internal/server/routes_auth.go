package server

import (
	"net/http"

	"github.com/gin-gonic/gin"
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
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if err := c.ShouldBindJSON(&body); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": "invalid body"})
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
