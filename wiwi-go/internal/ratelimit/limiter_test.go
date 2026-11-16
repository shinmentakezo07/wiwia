package ratelimit

import "testing"

func TestAllowAndDeny(t *testing.T) {
	l := New()
	for i := 0; i < 3; i++ {
		if ok, _ := l.Allow("k", 3, 0, 0); !ok {
			t.Fatalf("request %d should be allowed", i)
		}
	}
	if ok, retry := l.Allow("k", 3, 0, 0); ok || retry <= 0 {
		t.Fatalf("expected denial with retry, ok=%v retry=%v", ok, retry)
	}
	// unlimited
	if ok, _ := l.Allow("free", 0, 0, 0); !ok {
		t.Fatal("rpm 0 must be unlimited")
	}
}

func TestTokenLimit(t *testing.T) {
	l := New()
	if ok, _ := l.Allow("t", 0, 100, 60); !ok {
		t.Fatal("first should pass")
	}
	if ok, _ := l.Allow("t", 0, 100, 60); ok {
		t.Fatal("second should exceed tpm")
	}
}
