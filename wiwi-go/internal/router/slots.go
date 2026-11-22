package router

import (
	"fmt"
	"time"
)

// depWindow is a per-deployment sliding window of admission events. It exists
// only for deployments that declare an rpm/tpm cap; an uncapped deployment pays
// nothing (Python AUDIT #101).
type depWindow struct {
	events []depEvent
}

type depEvent struct {
	at        time.Time
	tokens    int
	id        string
	estimated bool
}

func (w *depWindow) prune(now time.Time) {
	cutoff := now.Add(-rateWindow)
	live := w.events[:0]
	for _, e := range w.events {
		if e.at.After(cutoff) {
			live = append(live, e)
		}
	}
	w.events = live
}

func (w *depWindow) total() int {
	t := 0
	for _, e := range w.events {
		t += e.tokens
	}
	return t
}

const rateWindow = 60 * time.Second

// RateLimited reports whether admitting estTokens would cross this
// deployment's rpm/tpm cap.
func (d *Deployment) RateLimited(now time.Time, estTokens int) bool {
	if d.RPM == nil && d.TPM == nil {
		return false
	}
	if estTokens < 0 {
		estTokens = 0
	}
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.RPM != nil && *d.RPM > 0 {
		w := d.rpmWindowLocked()
		w.prune(now)
		if w.total()+1 > *d.RPM {
			return true
		}
	}
	if d.TPM != nil && *d.TPM > 0 {
		w := d.tpmWindowLocked()
		w.prune(now)
		if w.total()+estTokens > *d.TPM {
			return true
		}
	}
	return false
}

// ReserveDepSlot takes this deployment's rpm/tpm slot for an admitted request.
// Both events start estimated; SettleDepTokens clears the flag once usage is
// known and RefundDepSlot refunds only still-estimated events, so a completed
// request can never be reclaimed by a late refund.
func (d *Deployment) ReserveDepSlot(requestID string, estTokens int) {
	if d.RPM == nil && d.TPM == nil {
		return
	}
	if estTokens < 0 {
		estTokens = 0
	}
	now := time.Now()
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.RPM != nil && *d.RPM > 0 {
		w := d.rpmWindowLocked()
		w.events = append(w.events, depEvent{at: now, tokens: 1, id: requestID, estimated: true})
	}
	if d.TPM != nil && *d.TPM > 0 {
		w := d.tpmWindowLocked()
		w.events = append(w.events, depEvent{at: now, tokens: estTokens, id: requestID, estimated: true})
	}
}

// SettleDepTokens replaces this request's tpm estimate with actual usage and
// stops either slot from being refundable. Idempotent: a repeated settle
// adjusts the existing event instead of appending a second one.
func (d *Deployment) SettleDepTokens(requestID string, tokens int) {
	if d.RPM == nil && d.TPM == nil {
		return
	}
	if tokens < 0 {
		tokens = 0
	}
	now := time.Now()
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.TPM != nil && *d.TPM > 0 {
		w := d.tpmWindowLocked()
		w.prune(now)
		found := false
		for i := len(w.events) - 1; i >= 0; i-- {
			if w.events[i].id == requestID {
				w.events[i].tokens = tokens
				w.events[i].estimated = false
				found = true
				break
			}
		}
		if !found {
			// The reservation aged out mid-stream (or this is a resume onto a
			// deployment that never admitted the original): record the real
			// usage so the window still accounts for billed tokens.
			w.events = append(w.events, depEvent{at: now, tokens: tokens, id: requestID})
		}
	}
	if d.RPM != nil && *d.RPM > 0 {
		w := d.rpmWindowLocked()
		w.prune(now)
		for i := len(w.events) - 1; i >= 0; i-- {
			if w.events[i].id == requestID {
				w.events[i].estimated = false
				break
			}
		}
	}
}

// RefundDepSlot refunds an admitted-but-unpriced request's rpm/tpm slots.
// Only estimated events are removed, so a request that delivered usage keeps
// its slot and stays billed against the cap.
func (d *Deployment) RefundDepSlot(requestID string) {
	if d.RPM == nil && d.TPM == nil {
		return
	}
	now := time.Now()
	d.mu.Lock()
	defer d.mu.Unlock()
	if d.TPM != nil && *d.TPM > 0 {
		w := d.tpmWindowLocked()
		w.prune(now)
		w.dropEstimated(requestID)
	}
	if d.RPM != nil && *d.RPM > 0 {
		w := d.rpmWindowLocked()
		w.prune(now)
		w.dropEstimated(requestID)
	}
}

func (w *depWindow) dropEstimated(requestID string) {
	live := w.events[:0]
	for _, e := range w.events {
		if e.id == requestID && e.estimated {
			continue
		}
		live = append(live, e)
	}
	w.events = live
}

// RetryAfterS is the seconds until admission can succeed again, clamped to
// 1..60. Only a saturated window constrains the horizon, so a full rpm window
// never reports the expiry of an unrelated cheap tpm event (Python AUDIT #303).
func (d *Deployment) RetryAfterS(now time.Time) int {
	d.mu.Lock()
	defer d.mu.Unlock()
	horizon := 0.0
	saturated := false
	consider := func(w *depWindow, limit *int) {
		if w == nil || limit == nil || *limit <= 0 {
			return
		}
		w.prune(now)
		if w.total() < *limit {
			return
		}
		saturated = true
		if len(w.events) > 0 {
			if h := rateWindow.Seconds() - now.Sub(w.events[0].at).Seconds(); h > horizon {
				horizon = h
			}
		} else {
			horizon = rateWindow.Seconds()
		}
	}
	consider(d.rpmWindow, d.RPM)
	consider(d.tpmWindow, d.TPM)
	if !saturated {
		return 1
	}
	n := int(horizon) + 1
	if n < 1 {
		return 1
	}
	if n > 60 {
		return 60
	}
	return n
}

func (d *Deployment) rpmWindowLocked() *depWindow {
	if d.rpmWindow == nil {
		d.rpmWindow = &depWindow{}
	}
	return d.rpmWindow
}

func (d *Deployment) tpmWindowLocked() *depWindow {
	if d.tpmWindow == nil {
		d.tpmWindow = &depWindow{}
	}
	return d.tpmWindow
}

// RateCapError reports that every healthy deployment in a group is at its
// per-deployment rpm/tpm cap. It is a 429 with a real horizon, deliberately
// distinct from "nothing healthy" (a 503 outage).
type RateCapError struct {
	Group      string
	RetryAfter int
}

func (e *RateCapError) Error() string {
	return fmt.Sprintf("all deployments for %q are at their rpm/tpm cap", e.Group)
}
