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
