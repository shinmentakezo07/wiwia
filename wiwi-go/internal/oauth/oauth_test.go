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
