package ratelimit

import (
	"context"
	"fmt"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// RateLimiter is the shared contract between the in-memory and Redis limiters.
//
// Reserve is admission; Settle reconciles the estimate with real usage; Refund
// gives back a reservation whose request never ran. Any implementation must
// keep those three consistent, or a failed request throttles the window.
type RateLimiter interface {
	Reserve(bucket, requestID string, rpm, tpm, estTokens int) (ok bool, retryAfter float64)
	Settle(bucket, requestID string, tokens int)
	Refund(bucket, requestID string)
}

// RateLimiterLegacy is the pre-reservation API surface. Both implementations
// satisfy it through Allow, which is Reserve without identity.
type RateLimiterLegacy interface {
	Allow(bucket string, rpm, tpm, estTokens int) (ok bool, retryAfter float64)
}

// RedisLimiter shares rpm/tpm caps across gateway workers through a sliding
// window per scope: a sorted set holds one member per admitted request (score =
// admission time) and a hash holds its token estimate. Settlement rewrites the
// token count and clears the estimated flag; refunds remove only still-estimated
// members.
type RedisLimiter struct {
	client *redis.Client
	prefix string
	seq    atomic.Uint64
}

func NewRedis(redisURL string) (*RedisLimiter, error) {
	opts, err := redis.ParseURL(redisURL)
	if err != nil {
		return nil, err
	}
	return &RedisLimiter{client: redis.NewClient(opts), prefix: "wiwi:rl:"}, nil
}

func (l *RedisLimiter) rpmKey(bucket string) string { return l.prefix + "rpm:" + bucket }
func (l *RedisLimiter) tpmKey(bucket string) string { return l.prefix + "tpm:" + bucket }
func (l *RedisLimiter) tokensKey(bucket string) string {
	return l.prefix + "tpmtok:" + bucket
}
func (l *RedisLimiter) estKey(bucket string) string { return l.prefix + "est:" + bucket }

// Allow is Reserve without identity.
func (l *RedisLimiter) Allow(bucket string, rpm, tpm, estTokens int) (bool, float64) {
	return l.Reserve(bucket, "", rpm, tpm, estTokens)
}

func (l *RedisLimiter) Reserve(bucket, requestID string, rpm, tpm, estTokens int) (bool, float64) {
	if rpm <= 0 && tpm <= 0 {
		return true, 0
	}
	if estTokens < 0 {
		estTokens = 0
	}
	// A reservation must be identifiable to be refundable later.
	if requestID == "" {
		requestID = fmt.Sprintf("anon-%d-%d", time.Now().UnixNano(), l.seq.Add(1))
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()

	now := float64(time.Now().UnixMilli()) / 1000.0
	cutoff := now - window.Seconds()

	if rpm > 0 {
		key := l.rpmKey(bucket)
		if err := l.client.ZRemRangeByScore(ctx, key, "-inf", strconv.FormatFloat(cutoff, 'f', -1, 64)).Err(); err != nil {
			return true, 0 // fail open: Redis unreachable must not break serving
		}
		n, err := l.client.ZCard(ctx, key).Result()
		if err != nil {
			return true, 0
		}
		if int(n) >= rpm {
			return false, l.oldestHorizon(ctx, key, now)
		}
	}
	if tpm > 0 {
		key := l.tpmKey(bucket)
		if err := l.client.ZRemRangeByScore(ctx, key, "-inf", strconv.FormatFloat(cutoff, 'f', -1, 64)).Err(); err != nil {
			return true, 0
		}
		used, err := l.liveTokens(ctx, bucket)
		if err != nil {
			return true, 0
		}
		if used+estTokens > tpm {
			return false, l.tokenHorizon(ctx, bucket, used+estTokens-tpm, now)
		}
	}

	pipe := l.client.Pipeline()
	if rpm > 0 {
		pipe.ZAdd(ctx, l.rpmKey(bucket), redis.Z{Score: now, Member: requestID})
		// Mark the rpm reservation estimated so Refund can remove it: the
		// hash field carries the rpm-ness, and without it Refund's estKey
		// check never matches — an rpm slot would never be refundable.
		pipe.HSet(ctx, l.estKey(bucket), "rpm:"+requestID, 1)
	}
	if tpm > 0 {
		pipe.ZAdd(ctx, l.tpmKey(bucket), redis.Z{Score: now, Member: requestID})
		pipe.HSet(ctx, l.tokensKey(bucket), requestID, estTokens)
		pipe.HSet(ctx, l.estKey(bucket), requestID, 1)
	}
	if _, err := pipe.Exec(ctx); err != nil {
		return true, 0 // fail open; the reservation is best-effort
	}
	if rpm > 0 || tpm > 0 {
		// Expire well past the window so a quiet key does not leak forever.
		keep := window + time.Minute
		if rpm > 0 {
			l.client.Expire(ctx, l.rpmKey(bucket), keep)
		}
		if tpm > 0 {
			l.client.Expire(ctx, l.tpmKey(bucket), keep)
			l.client.Expire(ctx, l.tokensKey(bucket), keep)
			l.client.Expire(ctx, l.estKey(bucket), keep)
		}
	}
	return true, 0
}

func (l *RedisLimiter) Settle(bucket, requestID string, tokens int) {
	if tokens < 0 {
		tokens = 0
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()

	pipe := l.client.Pipeline()
	pipe.HSet(ctx, l.tokensKey(bucket), requestID, tokens)
	pipe.HSet(ctx, l.estKey(bucket), requestID, 0)
	if _, err := pipe.Exec(ctx); err != nil {
		return
	}
	// Settle the rpm event too, so a completed request keeps its slot rather
	// than staying refundable.
	l.client.HSet(ctx, l.estKey(bucket), "rpm:"+requestID, 0)
}

func (l *RedisLimiter) Refund(bucket, requestID string) {
	if requestID == "" {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()

	// The estimated-check and the removal must be one atomic step, or a refund
	// racing a settle could reclaim a just-billed slot (TOCTOU). Lua runs
	// atomically on the Redis side.
	// KEYS[1]=est hash KEYS[2]=rpm zset KEYS[3]=tpm zset KEYS[4]=tokens hash
	// ARGV[1]=tpm flag ARGV[2]=rpm flag
	const refundScript = `
local est, rpm, tpm, toks = KEYS[1], KEYS[2], KEYS[3], KEYS[4]
local removed = 0
if redis.call('HGET', est, ARGV[2]) == '1' then
	redis.call('ZREM', rpm, ARGV[3])
	redis.call('HDEL', est, ARGV[2])
	removed = removed + 1
end
if redis.call('HGET', est, ARGV[1]) == '1' then
	redis.call('ZREM', tpm, ARGV[3])
	redis.call('HDEL', toks, ARGV[3])
	redis.call('HDEL', est, ARGV[1])
	removed = removed + 1
end
return removed`
	_, _ = l.client.Eval(ctx, refundScript, []string{
		l.estKey(bucket), l.rpmKey(bucket), l.tpmKey(bucket), l.tokensKey(bucket),
	}, requestID, "rpm:"+requestID, requestID).Result()
}

// liveTokens sums the token estimates of members still inside the window.
func (l *RedisLimiter) liveTokens(ctx context.Context, bucket string) (int, error) {
	key := l.tpmKey(bucket)
	members, err := l.client.ZRange(ctx, key, 0, -1).Result()
	if err != nil {
		return 0, err
	}
	if len(members) == 0 {
		return 0, nil
	}
	vals, err := l.client.HMGet(ctx, l.tokensKey(bucket), members...).Result()
	if err != nil {
		return 0, err
	}
	total := 0
	for i, v := range vals {
		n := 0
		switch x := v.(type) {
		case string:
			n, _ = strconv.Atoi(x)
		case int64:
			n = int(x)
		}
		if v == nil {
			// Member without a token record: charge at least one token so the
			// window is never free.
			n = 1
			l.client.HSet(ctx, l.tokensKey(bucket), members[i], n)
		}
		total += n
	}
	return total, nil
}

// tokenHorizon is the wait until enough of the oldest reservations age out to
// free need tokens.
func (l *RedisLimiter) tokenHorizon(ctx context.Context, bucket string, need int, now float64) float64 {
	pairs, err := l.client.ZRangeWithScores(ctx, l.tpmKey(bucket), 0, -1).Result()
	if err != nil {
		return window.Seconds()
	}
	members := make([]string, 0, len(pairs))
	for _, p := range pairs {
		members = append(members, fmt.Sprint(p.Member))
	}
	if len(members) == 0 {
		return window.Seconds()
	}
	vals, _ := l.client.HMGet(ctx, l.tokensKey(bucket), members...).Result()
	freed := 0
	for i, p := range pairs {
		n := 1
		if i < len(vals) {
			switch x := vals[i].(type) {
			case string:
				if parsed, err := strconv.Atoi(x); err == nil {
					n = parsed
				}
			case int64:
				n = int(x)
			}
		}
		freed += n
		if freed >= need {
			return clampWait(p.Score + window.Seconds() - now)
		}
	}
	return window.Seconds()
}

// oldestHorizon is the time until the oldest admitted request leaves the window.
func (l *RedisLimiter) oldestHorizon(ctx context.Context, key string, now float64) float64 {
	pairs, err := l.client.ZRangeWithScores(ctx, key, 0, 0).Result()
	if err != nil || len(pairs) == 0 {
		return window.Seconds()
	}
	return clampWait(pairs[0].Score + window.Seconds() - now)
}

func clampWait(w float64) float64 {
	if w <= 0 {
		return 0.001
	}
	return w
}

func (l *RedisLimiter) Close() error { return l.client.Close() }
