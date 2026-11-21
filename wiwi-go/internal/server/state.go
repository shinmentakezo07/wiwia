package server

import (
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"

	"wiwi-go/internal/auth"
	"wiwi-go/internal/cache"
	"wiwi-go/internal/config"
	"wiwi-go/internal/core"
	"wiwi-go/internal/cost"
	"wiwi-go/internal/logging"
	"wiwi-go/internal/metrics"
	"wiwi-go/internal/providers"
	"wiwi-go/internal/ratelimit"
	"wiwi-go/internal/router"
	"wiwi-go/internal/store"
	"wiwi-go/internal/streaming"
	"wiwi-go/internal/telemetry"
	"wiwi-go/internal/versions"
)

// AppState holds every long-lived subsystem. It is built once at startup.
type AppState struct {
	Cfg           *config.Config
	Store         *store.Store
	Auth          *auth.Service
	Router        *router.Router
	Gateway       *core.Gateway
	Cost          *cost.Engine
	Limiter       ratelimit.RateLimiter
	Cache         cache.Store
	Flight        *cache.Flight
	Bus           *logging.Bus
	Totals        *logging.Totals
	Metrics       *metrics.Collector
	Journal       *streaming.Journal
	Responses     *ResponseStore
	SessionSecret string
	StaticDir     string
	StartTime     time.Time

	SpendBuffer *auth.SpendBuffer
	Healer      *core.Healer
	// Abuse throttles for the unauthenticated auth surface (Python parity:
	// 10 failed logins / 5 min per IP+account, 5 signups / hour per IP).
	LoginThrottle  *attemptThrottle
	SignupThrottle *attemptThrottle
	// Pending redirect-OAuth sessions (Python's oauth_pending map). The state
	// token is single-use and authorizes one public callback.
	OAuthMu      sync.Mutex
	OAuthPending map[string]oauthSession
	// Playground key plaintexts, bounded and LRU-evicted (Python's
	// state.pg_bearers). Keyed by actor id.
	PGBearers *pgBearerCache
	// Process-lifetime operator counters that cannot be derived from the
	// request-event ring. Read atomically by /health and the Prometheus
	// exporter so the two surfaces cannot disagree (AUDIT #347).
	SpendChargeFailures  atomic.Int64
	RealtimeUsageMissing atomic.Int64
	stop                 chan struct{}
	stopOnce             sync.Once
}

// App bundles state and the Gin engine.
type App struct {
	State  *AppState
	Router *gin.Engine
}

// New builds the app. It opens the DB, migrates, constructs subsystems, and
// registers every route.
func New(cfg *config.Config) (*App, error) {
	cfg.ApplyDefaults()
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	st, err := store.Open(cfg.General.DatabaseURL)
	if err != nil {
		return nil, err
	}
	if err := st.Migrate(); err != nil {
		return nil, err
	}
	st.ConfigurePool(cfg.General.DBPoolSize, cfg.General.DBMaxOverflow,
		time.Duration(cfg.General.DBPoolRecycleS*float64(time.Second)))
	// Reclaim budget reservations stranded by a crashed process. In a shared
	// database only reservations older than reservation_stale_s are cleared.
	if cfg.General.SharedDatabase {
		cutoff := time.Now().Unix() - int64(cfg.General.ReservationStaleS)
		_ = st.Exec(st.Rebind(`UPDATE vkeys SET budget_reserved=0, reserved_at=NULL WHERE reserved_at IS NOT NULL AND reserved_at < ?`), cutoff)
	} else {
		_ = st.Exec(`UPDATE vkeys SET budget_reserved=0, reserved_at=NULL`)
	}
	loadOverlay(cfg, st)
	loadRuntimeSettings(cfg, st)
	costEngine := cost.NewEngine()
	loadPrices(st, costEngine)
	rt := router.New(cfg)
	var limiter ratelimit.RateLimiter = ratelimit.New()
	var respCache cache.Store = cache.New(cfg.Cache.Enabled, cfg.Cache.TTLS, cfg.Cache.MaxEntries)
	if cfg.General.RedisURL != "" {
		if rl, err := ratelimit.NewRedis(cfg.General.RedisURL); err == nil {
			limiter = rl
		} else {
			log.Printf("wiwi: redis rate limiter unavailable, using in-memory: %v", err)
		}
		if rc, err := cache.NewRedis(cfg.General.RedisURL, cfg.Cache.Enabled, cfg.Cache.TTLS); err == nil {
			respCache = rc
		} else {
			log.Printf("wiwi: redis cache unavailable, using in-memory: %v", err)
		}
	}
	gw := core.New(rt, costEngine)
	state := &AppState{
		Cfg: cfg, Store: st, Auth: auth.NewService(st), Router: rt, Gateway: gw,
		Cost: costEngine, Limiter: limiter,
		Cache:  respCache,
		Flight: cache.NewFlightWithLimits(cfg.Wiwi.SingleflightMaxFlights, cfg.Wiwi.SingleflightMaxWaiters),
		Bus:    logging.NewBus(4096), Totals: logging.NewTotals(), Metrics: metrics.NewCollector(),
		Journal:        streaming.NewJournal(cfg.Router.StreamJournalEnabled, cfg.Router.StreamJournalDir, cfg.Router.StreamJournalTTLS, int64(cfg.Router.StreamJournalMaxBytes)),
		Responses:      NewResponseStore(st, cfg.Wiwi.StoreResponses, cfg.Wiwi.ResponseStoreTTLS),
		SessionSecret:  deriveSessionSecret(cfg),
		LoginThrottle:  newAttemptThrottle(10, 5*time.Minute),
		SignupThrottle: newAttemptThrottle(5, time.Hour),
		OAuthPending:   map[string]oauthSession{},
		PGBearers:      newPGBearerCache(pgBearerCacheMax),
		StaticDir:      staticDir(), StartTime: time.Now(),
		stop: make(chan struct{}),
	}
	state.Journal.Sweep()
	state.Responses.Start(cfg.Wiwi.ResponseStoreSweepInterval)
	providers.ConfigureToolSchemaCache(cfg.Wiwi.ToolSchemaCacheEntries)
	providers.ConfigureHTTPPool(cfg.Wiwi.HTTPMaxConnections, cfg.Wiwi.HTTPMaxKeepalive,
		time.Duration(cfg.Wiwi.HTTPKeepaliveExpiryS*float64(time.Second)))
	tracer := telemetry.NewWithOTLP(cfg.Telemetry.Enabled, state.Bus, cfg.Telemetry.ServiceName, cfg.Telemetry.Endpoint, cfg.Telemetry.Headers, cfg.Telemetry.SampleRatio, cfg.Telemetry.ExportTimeoutS)
	state.Gateway.Tracer = tracer
	state.Gateway.StreamCoalesce = cfg.Router.StreamCoalesce
	state.Gateway.CoalesceMaxBytes = cfg.Router.StreamCoalesceMaxBytes
	state.Gateway.CoalesceMaxMS = cfg.Router.StreamCoalesceMaxMS
	state.Gateway.StreamIdleTimeoutS = cfg.Router.StreamIdleTimeoutS
	state.Gateway.StreamFirstChunkTimeoutS = cfg.Router.StreamFirstChunkTimeoutS
	state.Gateway.StreamResumeMode = cfg.Router.StreamResume
	state.Gateway.ResumeMaxRetries = cfg.Router.StreamResumeMaxRetries
	state.Gateway.DropParams = cfg.Wiwi.DropParams
	if cfg.Router.StreamLoopLimit > 0 {
		state.Gateway.LoopLimit = cfg.Router.StreamLoopLimit
	}

	if cfg.Healer.Enabled {
		h := core.NewHealer(rt, cfg.Healer.TickS, cfg.Healer.ProbeTimeoutS, cfg.Healer.MaxProbesPerSweep)
		if cfg.Healer.ProbeBackoffBaseS > 0 {
			h.ProbeBackoffBase = time.Duration(cfg.Healer.ProbeBackoffBaseS * float64(time.Second))
		}
		if cfg.Healer.ProbeBackoffCapS > 0 {
			h.ProbeBackoffCap = time.Duration(cfg.Healer.ProbeBackoffCapS * float64(time.Second))
		}
		h.Start()
		state.Healer = h
		log.Printf("wiwi: health healer started")
	}

	if missing := providers.MissingProviders(config.ProviderTypes()); len(missing) > 0 {
		log.Printf("wiwi: WARNING provider types without an adapter: %v", missing)
	}

	gin.SetMode(gin.ReleaseMode)
	r := gin.New()
	// Match on the raw (still-escaped) path so a group name that itself
	// contains a slash — the SPA uses model ids like "z-ai/glm-5" as group
	// names, encoded as %2F like the Python backend's {name:path} route —
	// stays one route segment. With the default decoded matching, net/http
	// turns %2F into a real path separator first and the request falls
	// through to NoRoute ("not found"). Params still arrive unescaped
	// (UnescapePathValues defaults true).
	r.UseRawPath = true
	r.Use(gin.Recovery())
	app := &App{State: state, Router: r}
	app.registerMiddleware()
	app.registerRoutes()
	app.startOAuthRefresher()
	versions.Start(state.stop)
	app.startRetention()
	return app, nil
}

func deriveSessionSecret(cfg *config.Config) string {
	if v := os.Getenv("WIWI_SESSION_SECRET"); v != "" {
		return v
	}
	if cfg.General.MasterKey != "" {
		return "session:" + cfg.General.MasterKey
	}
	return "wiwi-insecure-session-secret"
}

func staticDir() string {
	if v := os.Getenv("WIWI_STATIC_DIR"); v != "" {
		return v
	}
	for _, p := range []string{"wiwi/server/static", "../wiwi/server/static", "static"} {
		if fi, err := os.Stat(p); err == nil && fi.IsDir() {
			abs, _ := filepath.Abs(p)
			return abs
		}
	}
	return ""
}

func (a *App) registerMiddleware() {
	r := a.Router
	r.Use(func(c *gin.Context) {
		rid := c.GetHeader("x-wiwi-request-id")
		if rid == "" {
			rid = uuid.NewString()[:16]
		}
		c.Set("request_id", rid)
		c.Header("x-wiwi-request-id", rid)
		c.Next()
		c.Header("x-wiwi-latency-ms", itoa(int(time.Since(a.State.StartTime).Milliseconds())))
	})
	maxBytes := int64(a.State.Cfg.Wiwi.MaxRequestBodyMB) << 20
	if maxBytes <= 0 {
		maxBytes = 50 << 20
	}
	r.Use(func(c *gin.Context) {
		if c.Request.Body != nil {
			c.Request.Body = http.MaxBytesReader(c.Writer, c.Request.Body, maxBytes)
		}
		c.Next()
	})
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

// bearer extracts the credential from Authorization or x-api-key.
func bearer(c *gin.Context) string {
	h := c.GetHeader("Authorization")
	if h != "" {
		if len(h) > 7 && strings.EqualFold(h[:7], "Bearer ") {
			return strings.TrimSpace(h[7:])
		}
		return strings.TrimSpace(h)
	}
	if k := c.GetHeader("x-api-key"); k != "" {
		return strings.TrimSpace(k)
	}
	return ""
}

// resolveAuth maps a request credential to an AuthInfo (master or virtual key).
func (a *App) resolveAuth(c *gin.Context) (*core.AuthInfo, bool) {
	key := bearer(c)
	if key == "" {
		return nil, false
	}
	if key == a.State.Cfg.General.MasterKey {
		return &core.AuthInfo{KeyID: "master", KeyName: "master", IsMaster: true, Role: "admin"}, true
	}
	v, err := a.State.Auth.Authenticate(key)
	if err != nil {
		return nil, false
	}
	info := &core.AuthInfo{
		KeyID: v.ID, KeyName: v.Name, MaxBudget: derefF(v.MaxBudget), Spend: v.Spend,
		Models: v.Models, RPM: derefI(v.RPM), TPM: derefI(v.TPM),
	}
	return info, true
}

func derefF(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}
func derefI(p *int) int {
	if p == nil {
		return 0
	}
	return *p
}

// requireAPI enforces virtual-key/master auth on /v1 routes.
func (a *App) requireAPI(c *gin.Context) {
	info, ok := a.resolveAuth(c)
	if !ok {
		_, body := wireError(core.SurfaceChat, 401, "authentication_error", "invalid or missing API key")
		c.AbortWithStatusJSON(http.StatusUnauthorized, body)
		return
	}
	c.Set("auth", info)
	c.Next()
}

// requireAdmin enforces the master key (or an admin session cookie).
func (a *App) requireAdmin(c *gin.Context) {
	key := bearer(c)
	if key != "" && key == a.State.Cfg.General.MasterKey {
		c.Set("auth", &core.AuthInfo{KeyID: "master", IsMaster: true, Role: "admin"})
		c.Next()
		return
	}
	if cookie, err := c.Cookie("wiwi_session"); err == nil && cookie != "" {
		if u, err := a.State.Auth.ValidateSession(a.State.SessionSecret, cookie); err == nil && u.Role == "admin" {
			c.Set("auth", &core.AuthInfo{KeyID: u.ID, UserID: u.ID, Role: "admin", KeyName: u.Username})
			c.Next()
			return
		}
	}
	c.AbortWithStatusJSON(http.StatusUnauthorized, gin.H{"error": "unauthorized"})
}

// registerRoutes wires every surface.
func (a *App) registerRoutes() {
	r := a.Router

	r.GET("/health", a.handleHealth)
	r.GET("/cline/oauth/callback", a.clineOAuthCallback)
	r.GET("/v1/models", a.requireAPI, a.handleModels)
	r.GET("/public/models", a.handlePublicModels)
	// Always registered: when disabled the handler answers 404 with the
	// documented body, exactly as Python does. Conditional registration made
	// the path fall through to the SPA's index.html, which scrape clients read
	// as a successful scrape with zero series.
	p := a.State.Cfg.Router.PrometheusPath
	if p == "" {
		p = "/metrics"
	}
	r.GET(p, a.handleMetrics)
	r.GET("/v1/realtime", a.requireRealtimeAuth, a.handleRealtime)

	v1 := r.Group("/v1", a.requireAPI)
	v1.POST("/chat/completions", a.handleChatCompletions)
	// Behind the session cookie, not a bearer: the playground key is resolved
	// server-side exactly as Python's route does. Registering it inside the
	// requireAPI group made a logged-in browser's playground 401 with
	// "invalid or missing API key".
	r.POST("/v1/playground/completions", a.requirePlayground, a.handleChatCompletions)
	v1.POST("/completions", a.handleLegacyCompletions)
	v1.POST("/responses", a.handleResponses)
	v1.GET("/responses/:id", a.handleGetResponse)
	v1.DELETE("/responses/:id", a.handleDeleteResponse)
	v1.POST("/messages", a.handleMessages)
	v1.POST("/messages/count_tokens", a.handleCountTokens)

	authGroup := r.Group("/auth")
	authGroup.POST("/signup", a.handleSignup)
	authGroup.POST("/login", a.handleLogin)
	authGroup.POST("/logout", a.handleLogout)
	authGroup.GET("/me", a.handleMe)
	authGroup.POST("/playground-key", a.handlePlaygroundKey)

	admin := r.Group("/admin", a.requireAdmin)
	a.registerAdminRoutes(admin)

	a.registerStatic()
}

// Close stops background services and closes the store.
func (a *App) Close() {
	a.State.stopOnce.Do(func() {
		if a.State.stop != nil {
			close(a.State.stop)
		}
	})
	if a.State.Healer != nil {
		a.State.Healer.Stop()
	}
	if a.State.Responses != nil {
		a.State.Responses.Stop()
	}
	if a.State.Store != nil {
		_ = a.State.Store.Close()
	}
}
