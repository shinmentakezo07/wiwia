package auth

import (
	"testing"

	"wiwi-go/internal/store"
)

func newSvc(t *testing.T) *Service {
	t.Helper()
	s, err := store.Open(":memory:")
	if err != nil {
		t.Fatal(err)
	}
	if err := s.Migrate(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return NewService(s)
}

func TestGenerateAuthenticate(t *testing.T) {
	svc := newSvc(t)
	plain, v, err := svc.Generate("test", nil, nil, nil, nil, 0)
	if err != nil {
		t.Fatal(err)
	}
	if v.ID == "" || len(plain) < 10 {
		t.Fatalf("bad key: %q %v", plain, v)
	}
	got, err := svc.Authenticate(plain)
	if err != nil {
		t.Fatalf("auth: %v", err)
	}
	if got.ID != v.ID {
		t.Fatalf("id mismatch")
	}
	if _, err := svc.Authenticate("sk-wiwi-wrong"); err == nil {
		t.Fatal("expected auth failure")
	}
}

func TestBudgetReserveSettle(t *testing.T) {
	svc := newSvc(t)
	_, v, _ := svc.Generate("b", floatPtr(1.0), nil, nil, nil, 0)
	if err := svc.ReserveBudget(v.ID, 0.6); err != nil {
		t.Fatalf("reserve: %v", err)
	}
	if err := svc.ReserveBudget(v.ID, 0.6); err != ErrBudgetExceeded {
		t.Fatalf("expected budget exceeded, got %v", err)
	}
	if err := svc.Settle(v.ID, 0.6, 0.4); err != nil {
		t.Fatalf("settle: %v", err)
	}
	got, _ := svc.Get(v.ID)
	if got.Spend != 0.4 {
		t.Fatalf("spend=%v", got.Spend)
	}
}

func TestUsersAndSessions(t *testing.T) {
	svc := newSvc(t)
	u, err := svc.CreateUser("alice", "pw", "admin")
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.AuthenticateUser("alice", "pw"); err != nil {
		t.Fatalf("login: %v", err)
	}
	if _, err := svc.AuthenticateUser("alice", "bad"); err == nil {
		t.Fatal("expected bad login")
	}
	sess, err := svc.NewSession("secret", u.ID, 3600)
	if err != nil {
		t.Fatal(err)
	}
	got, err := svc.ValidateSession("secret", sess)
	if err != nil || got.ID != u.ID {
		t.Fatalf("session: %v %v", got, err)
	}
	if _, err := svc.ValidateSession("wrong", sess); err == nil {
		t.Fatal("expected bad session secret")
	}
}

func floatPtr(f float64) *float64 { return &f }
