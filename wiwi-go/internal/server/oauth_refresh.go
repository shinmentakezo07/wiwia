package server

import (
	"log"
	"time"

	"wiwi-go/internal/config"
	"wiwi-go/internal/oauth"
)

// startOAuthRefresher rotates OAuth access tokens before they expire, mirroring
// the Python auto-refresh services. It is a no-op for a gateway with no
// OAuth-backed provider.
func (a *App) startOAuthRefresher() {
	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-ticker.C:
				a.refreshExpiringOAuth()
			case <-a.State.stop:
				return
			}
		}
	}()
}

func (a *App) refreshExpiringOAuth() {
	changed := false
	for _, name := range []string{"cline", "hermes", "workbuddy"} {
		p := a.findProvider(name)
		if p == nil {
			continue
		}
		switch p.ProviderType {
		case "cline":
			changed = a.refreshCline(p) || changed
		case "hermes":
			changed = a.refreshHermes(p) || changed
		case "workbuddy":
			changed = a.refreshWorkBuddy(p) || changed
		}
	}
	if changed {
		a.rebuild()
	}
}

func (a *App) refreshCline(p *config.Provider) bool {
	changed := false
	for i := range p.Keys {
		cred, ok := oauth.Decode(oauth.ClinePrefix, p.Keys[i].Key)
		if !ok || cred.RefreshToken == "" {
			continue
		}
		if !oauth.ExpiresWithinLead(cred.ExpiresAt, oauth.ClineRefreshLeadS) {
			continue
		}
		res, err := oauth.ClineRefresh(cred.RefreshToken)
		if err != nil || res == nil {
			if err != nil {
				log.Printf("wiwi: cline token refresh failed: %v", err)
			}
			continue
		}
		cred.AccessToken, _ = res["access_token"].(string)
		if rt, _ := res["refresh_token"].(string); rt != "" {
			cred.RefreshToken = rt
		}
		if s, _ := res["expires_at"].(string); s != "" {
			cred.ExpiresAt, _ = oauth.ParseISOTime(s)
		}
		p.Keys[i].Key = oauth.Encode(oauth.ClinePrefix, cred)
		changed = true
	}
	if changed {
		_ = saveProvider(a.State.Store, *p)
	}
	return changed
}

func (a *App) refreshHermes(p *config.Provider) bool {
	changed := false
	for i := range p.Keys {
		cred, ok := oauth.Decode(oauth.HermesPrefix, p.Keys[i].Key)
		if !ok || cred.RefreshToken == "" {
			continue
		}
		if !oauth.ExpiresWithinLead(cred.ExpiresAt, oauth.HermesRefreshLeadS) {
			continue
		}
		payload, err := oauth.HermesRefresh(cred.RefreshToken)
		if err != nil {
			log.Printf("wiwi: hermes token refresh failed: %v", err)
			continue
		}
		p.Keys[i].Key = oauth.Encode(oauth.HermesPrefix, oauth.HermesCredentialFromToken(payload))
		changed = true
	}
	if changed {
		_ = saveProvider(a.State.Store, *p)
	}
	return changed
}

func (a *App) refreshWorkBuddy(p *config.Provider) bool {
	changed := false
	for i := range p.Keys {
		cred, ok := oauth.Decode(oauth.WorkBuddyPrefix, p.Keys[i].Key)
		if !ok || cred.RefreshToken == "" {
			continue
		}
		if !oauth.ExpiresWithinLead(cred.ExpiresAt, oauth.WorkBuddyRefreshLeadS) {
			continue
		}
		nc, err := oauth.WorkBuddyRefresh(cred)
		if err != nil || nc == nil {
			continue
		}
		p.Keys[i].Key = oauth.Encode(oauth.WorkBuddyPrefix, *nc)
		changed = true
	}
	if changed {
		_ = saveProvider(a.State.Store, *p)
	}
	return changed
}
