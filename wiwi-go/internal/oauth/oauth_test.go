package oauth

import (
	"encoding/base64"
	"testing"
)

func TestCredentialRoundTrip(t *testing.T) {
	c := Credential{AccessToken: "a", RefreshToken: "r", ExpiresAt: 123, Email: "e@x"}
	enc := Encode(ClinePrefix, c)
	got, ok := Decode(ClinePrefix, enc)
	if !ok || got.AccessToken != "a" || got.RefreshToken != "r" || got.Email != "e@x" {
		t.Fatalf("round trip: %+v ok=%v", got, ok)
	}
	if _, ok := Decode(ClinePrefix, "not-a-credential"); ok {
		t.Fatal("junk decoded")
	}
}

func TestClineExchangeCode(t *testing.T) {
	payload := `{"accessToken":"acc","refreshToken":"ref","email":"me@example.com","expiresAt":"2027-01-01T00:00:00Z"}`
	code := base64.StdEncoding.EncodeToString([]byte(payload))
	out, err := ClineExchangeCode(code)
	if err != nil {
		t.Fatal(err)
	}
	if out["access_token"] != "acc" || out["refresh_token"] != "ref" || out["email"] != "me@example.com" {
		t.Fatalf("exchange: %v", out)
	}
	// A full callback URL with ?code= must also work.
	urlCode := "https://app.cline.bot/callback?code=" + code
	if _, err := ClineExchangeCode(urlCode); err != nil {
		t.Fatalf("url code: %v", err)
	}
}

func TestHermesCredential(t *testing.T) {
	payload := map[string]any{"access_token": "a", "refresh_token": "r", "expires_in": float64(3600)}
	c := HermesCredentialFromToken(payload)
	if c.AccessToken != "a" || c.ExpiresAt == 0 {
		t.Fatalf("hermes cred: %+v", c)
	}
	enc := Encode(HermesPrefix, c)
	if got, ok := Decode(HermesPrefix, enc); !ok || got.AccessToken != "a" {
		t.Fatalf("hermes decode: %+v %v", got, ok)
	}
}

func TestWorkBuddyAuthURLAllowlist(t *testing.T) {
	if acceptedAuthURL("https://www.workbuddy.ai/login") == "" {
		t.Fatal("expected allowed host")
	}
	if acceptedAuthURL("https://evil.example.com/login") != "" {
		t.Fatal("expected host rejection")
	}
}

func TestResolveExpiresAt(t *testing.T) {
	now := int64(1_000_000)
	lead := int64(120)
	// A usable value is honoured verbatim.
	if got := ResolveExpiresAt(float64(3600), lead, now); got != now+3600 {
		t.Fatalf("usable: %d", got)
	}
	// Missing, zero, boolean, <=lead and non-numeric values all fall back to
	// 2*lead so the token is not due on the very next sweep.
	for _, v := range []any{nil, float64(0), true, float64(60), "nope"} {
		if got := ResolveExpiresAt(v, lead, now); got != now+2*lead {
			t.Fatalf("unusable %v: %d", v, got)
		}
	}
}

func TestHermesMissingExpiryNoStorm(t *testing.T) {
	c := HermesCredentialFromToken(map[string]any{"access_token": "a", "refresh_token": "r"})
	if c.ExpiresAt == 0 {
		t.Fatal("missing expires_in left ExpiresAt zero; the refresh grant would be burned every sweep")
	}
	if ExpiresWithinLead(c.ExpiresAt, HermesRefreshLeadS) {
		t.Fatalf("fallback expiry %d is already due", c.ExpiresAt)
	}
}
