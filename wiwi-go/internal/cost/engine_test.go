package cost

import (
	"testing"

	"wiwi-go/internal/ir"
)

func TestComputePricedAndUnpriced(t *testing.T) {
	e := NewEngine()
	e.Upsert("m", ModelPrice{Prompt: 1e-6, Completion: 2e-6, Cached: 5e-7})
	c, priced := e.Compute("m", ir.Usage{PromptTokens: 1000, CompletionTokens: 500})
	if !priced {
		t.Fatal("expected priced")
	}
	want := 1000*1e-6 + 500*2e-6
	if c < want-1e-9 || c > want+1e-9 {
		t.Fatalf("cost=%v want %v", c, want)
	}
	if _, priced := e.Compute("nope", ir.Usage{}); priced {
		t.Fatal("expected unpriced")
	}
}

func TestDeleteAndAll(t *testing.T) {
	e := NewEngine()
	e.Upsert("x", ModelPrice{Prompt: 1})
	if _, ok := e.All()["x"]; !ok {
		t.Fatal("missing x")
	}
	e.Delete("x")
	if _, ok := e.All()["x"]; ok {
		t.Fatal("delete failed")
	}
}
