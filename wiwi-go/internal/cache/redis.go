package cache

import (
	"context"
	"time"

	"github.com/redis/go-redis/v9"
)

// Store is the response-cache contract shared by the in-memory and Redis
// backends.
type Store interface {
	Get(key string) ([]byte, bool)
	Set(key string, val []byte)
	Enabled() bool
}

// RedisCache is the shared response cache used when redis_url is configured, so
// entries are shared across gateway workers.
type RedisCache struct {
	client  *redis.Client
	enabled bool
	ttl     time.Duration
	prefix  string
}

func NewRedis(redisURL string, enabled bool, ttlS float64) (*RedisCache, error) {
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, err
	}
	if ttlS <= 0 {
		ttlS = 3600
	}
	return &RedisCache{
		client:  redis.NewClient(opts),
		enabled: enabled,
		ttl:     time.Duration(ttlS * float64(time.Second)),
		prefix:  "wiwi:cache:",
	}, nil
}

func (c *RedisCache) Enabled() bool { return c != nil && c.enabled }

func (c *RedisCache) Get(key string) ([]byte, bool) {
	if !c.Enabled() {
		return nil, false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	b, err := c.client.Get(ctx, c.prefix+key).Bytes()
	if err != nil {
		return nil, false // degrade to a miss; requests keep succeeding
	}
	return b, true
}

func (c *RedisCache) Set(key string, val []byte) {
	if !c.Enabled() {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	_ = c.client.Set(ctx, c.prefix+key, val, c.ttl).Err()
}

func (c *RedisCache) Close() error {
	if c == nil || c.client == nil {
		return nil
	}
	return c.client.Close()
}
