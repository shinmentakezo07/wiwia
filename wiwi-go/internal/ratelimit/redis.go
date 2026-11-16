package ratelimit

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// RateLimiter is the shared contract between the in-memory and Redis limiters.
type RateLimiter interface {
	Allow(bucket string, rpm, tpm, estTokens int) (ok bool, retryAfter float64)
}

// RedisLimiter shares rpm/tpm caps across gateway workers using fixed 60s
// windows (INCR + EXPIRE), so a per-key cap is global rather than per-process.
type RedisLimiter struct {
	client *redis.Client
	prefix string
}

func NewRedis(redisURL string) (*RedisLimiter, error) {
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, err
	}
	return &RedisLimiter{client: redis.NewClient(opts), prefix: "wiwi:rl:"}, nil
}

func (l *RedisLimiter) Allow(bucket string, rpm, tpm, estTokens int) (bool, float64) {
	if rpm <= 0 && tpm <= 0 {
		return true, 0
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	if rpm > 0 {
		key := l.prefix + "rpm:" + bucket
		n, err := l.client.Incr(ctx, key).Result()
		if err != nil {
			return true, 0 // fail open: Redis unreachable must not break serving
		}
		if n == 1 {
			l.client.Expire(ctx, key, 60*time.Second)
		}
		if int(n) > rpm {
			ttl, _ := l.client.TTL(ctx, key).Result()
			return false, ttl.Seconds()
		}
	}
	if tpm > 0 {
		key := l.prefix + "tpm:" + bucket
		n, err := l.client.IncrBy(ctx, key, int64(max(estTokens, 1))).Result()
		if err != nil {
			return true, 0
		}
		if n <= int64(max(estTokens, 1)) {
			l.client.Expire(ctx, key, 60*time.Second)
		}
		if int(n) > tpm {
			ttl, _ := l.client.TTL(ctx, key).Result()
			return false, ttl.Seconds()
		}
	}
	return true, 0
}

func (l *RedisLimiter) Close() error {
	if l == nil || l.client == nil {
		return nil
	}
	return l.client.Close()
}

var _ = fmt.Sprintf
