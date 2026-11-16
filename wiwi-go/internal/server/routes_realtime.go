package server

import (
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/gorilla/websocket"

	"wiwi-go/internal/providers"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

// handleRealtime relays a Realtime WebSocket session to an upstream provider.
// The model is taken from the `model` query parameter.
func (a *App) handleRealtime(c *gin.Context) {
	if !a.State.Cfg.Realtime.Enabled {
		c.JSON(http.StatusNotImplemented, gin.H{"error": "realtime is disabled"})
		return
	}
	// Authenticate: bearer, or ?key= when explicitly allowed.
	keyStr := bearer(c)
	if keyStr == "" && a.State.Cfg.Realtime.AllowKeyInQuery {
		keyStr = c.Query("key")
	}
	if keyStr == "" {
		c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid or missing API key"})
		return
	}
	if keyStr != a.State.Cfg.General.MasterKey {
		if _, err := a.State.Auth.Authenticate(keyStr); err != nil {
			c.JSON(http.StatusUnauthorized, gin.H{"error": "invalid or missing API key"})
			return
		}
	}
	model := c.Query("model")
	if model == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "model query parameter required"})
		return
	}
	group := a.State.Router.ResolveGroup(model)
	deps := a.State.Router.Deployments(group)
	if len(deps) == 0 {
		c.JSON(http.StatusNotFound, gin.H{"error": "model not found"})
		return
	}
	dep := deps[0]
	key, err := dep.PickKey(nil)
	if err != nil {
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": "no healthy key"})
		return
	}
	adapter, err := providers.FreshAdapter(dep.Provider.Type)
	if err != nil {
		c.JSON(http.StatusNotImplemented, gin.H{"error": "provider has no realtime surface"})
		return
	}
	base := strings.TrimRight(dep.Provider.BaseURL, "/")
	base = strings.Replace(base, "https://", "wss://", 1)
	base = strings.Replace(base, "http://", "ws://", 1)
	upURL := base + "/realtime"
	if rp, ok := adapter.(interface{ RealtimeURL(string) string }); ok {
		upURL = rp.RealtimeURL(dep.Provider.BaseURL)
	}
	upURL += "?model=" + url.QueryEscape(dep.Model)

	client, err := upgrader.Upgrade(c.Writer, c.Request, nil)
	if err != nil {
		return
	}
	defer client.Close()

	headers := http.Header{}
	for k, v := range adapter.Headers(providers.ProviderKeyRef{Label: key.Label, Secret: key.Secret}) {
		headers.Set(k, v)
	}
	upstream, _, err := websocket.DefaultDialer.Dial(upURL, headers)
	if err != nil {
		client.WriteMessage(websocket.TextMessage, []byte(`{"type":"error","error":{"message":"upstream realtime unavailable"}}`))
		return
	}
	defer upstream.Close()

	errc := make(chan error, 2)
	pump := func(dst, src *websocket.Conn) {
		for {
			mt, data, err := src.ReadMessage()
			if err != nil {
				errc <- err
				return
			}
			if err := dst.WriteMessage(mt, data); err != nil {
				errc <- err
				return
			}
		}
	}
	go pump(upstream, client)
	go pump(client, upstream)
	idle := time.Duration(a.State.Cfg.Realtime.IdleTimeoutS * float64(time.Second))
	maxSession := time.Duration(a.State.Cfg.Realtime.MaxSessionS * float64(time.Second))
	resetDeadlines := func() {
		if idle > 0 {
			_ = client.SetReadDeadline(time.Now().Add(idle))
			_ = upstream.SetReadDeadline(time.Now().Add(idle))
		}
	}
	resetDeadlines()
	stopKeepalive := make(chan struct{})
	if idle > 0 {
		go func() {
			ticker := time.NewTicker(idle / 2)
			defer ticker.Stop()
			for {
				select {
				case <-ticker.C:
					resetDeadlines()
				case <-stopKeepalive:
					return
				}
			}
		}()
	}
	defer close(stopKeepalive)
	select {
	case <-errc:
	case <-time.After(maxSession):
	case <-c.Request.Context().Done():
	}
}
