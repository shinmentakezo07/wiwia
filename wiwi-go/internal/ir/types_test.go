package ir

import "testing"

func TestEffortMapping(t *testing.T) {
	if b, ok := EffortToThinkingBudget("high"); !ok || b != 32000 {
		t.Fatalf("high -> %v %v", b, ok)
	}
	if _, ok := EffortToThinkingBudget("none"); ok {
		t.Fatal("none must be absent")
	}
	if _, ok := EffortToThinkingBudget("typo"); ok {
		t.Fatal("unknown must be absent")
	}
	medium := "medium"
	g := GenParams{ReasoningEffort: &medium}
	if g.EffectiveReasoningEffort() != "medium" {
		t.Fatal("effort")
	}
	disabled := "disabled"
	g2 := GenParams{ThinkingType: &disabled}
	if g2.EffectiveReasoningEffort() != "none" {
		t.Fatal("disabled must win")
	}
	if _, ok := g2.EffectiveThinkingBudget(); ok {
		t.Fatal("disabled budget must be off")
	}
}

// Unknown effort levels must leave thinking OFF (round-124 parity: Python
// returns None for an effort the budget table has not learned and does NOT
// fall through to the next knob). A fall-through re-enabled thinking at the
// medium default on a typo'd effort with thinking_type=enabled.
func TestUnknownEffortLeavesThinkingOff(t *testing.T) {
	typo := "hight"
	g := GenParams{ReasoningEffort: &typo}
	if b, ok := g.EffectiveThinkingBudget(); ok {
		t.Fatalf("unknown reasoning_effort must disable thinking, got %d", b)
	}
	g2 := GenParams{ReasoningEffort: &typo, Effort: &typo}
	if _, ok := g2.EffectiveThinkingBudget(); ok {
		t.Fatal("unknown efforts on both fields must disable thinking")
	}
	enabled := "enabled"
	g3 := GenParams{ReasoningEffort: &typo, ThinkingType: &enabled}
	if _, ok := g3.EffectiveThinkingBudget(); ok {
		t.Fatal("unknown effort must not fall through to the enabled-mode default")
	}
	// A KNOWN effort still wins over everything (precedence, most-specific
	// first) and a known secondary Effort resolves when ReasoningEffort is
	// unset.
	high := "high"
	if b, ok := (GenParams{ReasoningEffort: &high}).EffectiveThinkingBudget(); !ok || b != 32000 {
		t.Fatalf("high -> %v %v", b, ok)
	}
	if b, ok := (GenParams{Effort: &high}).EffectiveThinkingBudget(); !ok || b != 32000 {
		t.Fatalf("effort=high -> %v %v", b, ok)
	}
}
