package server

import (
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
)

func TestAttemptThrottleCountsAndResets(t *testing.T) {
	th := newAttemptThrottle(2, time.Minute)
	if ok, _ := th.TryConsume("ip"); !ok {
		t.Fatal("first attempt should pass")
	}
	if ok, _ := th.TryConsume("ip"); !ok {
		t.Fatal("second attempt should pass")
	}
	if ok, retry := th.TryConsume("ip"); ok || retry < 1 {
		t.Fatalf("third attempt should be refused with a retry hint, got ok=%v retry=%d", ok, retry)
	}
	th.Reset("ip")
	if ok, _ := th.TryConsume("ip"); !ok {
		t.Fatal("reset should clear the window")
	}
}

func TestAttemptThrottleRefund(t *testing.T) {
	th := newAttemptThrottle(1, time.Minute)
	th.TryConsume("ip")
	th.Refund("ip")
	if ok, _ := th.TryConsume("ip"); !ok {
		t.Fatal("refund should hand the reservation back")
	}
}

func ginCtxWith(remote, xff, xfp string) *gin.Context {
	c, _ := gin.CreateTestContext(httptest.NewRecorder())
	c.Request = httptest.NewRequest("POST", "/auth/login", nil)
	c.Request.RemoteAddr = remote
	if xff != "" {
		c.Request.Header.Set("X-Forwarded-For", xff)
	}
	if xfp != "" {
		c.Request.Header.Set("X-Forwarded-Proto", xfp)
	}
	return c
}

func TestClientIPTrustsForwardedOnlyFromTrustedPeer(t *testing.T) {
	trusted := []string{"10.0.0.0/8", "127.0.0.1"}
	if got := clientIP(ginCtxWith("10.1.2.3:5000", "203.0.113.9, 10.1.2.3", ""), trusted); got != "203.0.113.9" {
		t.Fatalf("trusted peer XFF = %q, want 203.0.113.9", got)
	}
	if got := clientIP(ginCtxWith("198.51.100.7:5000", "203.0.113.9", ""), trusted); got != "198.51.100.7" {
		t.Fatalf("untrusted peer must not be believed: got %q", got)
	}
	if got := clientIP(ginCtxWith("127.0.0.1:5000", "203.0.113.9", ""), trusted); got != "203.0.113.9" {
		t.Fatalf("bare trusted address must match: got %q", got)
	}
	if got := clientIP(ginCtxWith("10.1.2.3:5000", "203.0.113.9", ""), nil); got != "10.1.2.3" {
		t.Fatalf("empty trusted list must fail closed: got %q", got)
	}
}

func TestRequestIsHTTPSHonoursTrustedProto(t *testing.T) {
	trusted := []string{"10.0.0.0/8"}
	if !requestIsHTTPS(ginCtxWith("10.1.2.3:5000", "", "https"), trusted) {
		t.Fatal("trusted X-Forwarded-Proto https should be believed")
	}
	if requestIsHTTPS(ginCtxWith("198.51.100.7:5000", "", "https"), trusted) {
		t.Fatal("untrusted X-Forwarded-Proto must not be believed")
	}
}
