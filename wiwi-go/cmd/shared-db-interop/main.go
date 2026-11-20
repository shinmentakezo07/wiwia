// Command shared-db-interop runs one interop phase against a SQLite file the
// Python backend created (or vice versa). It exists only for the pytest suite:
// tests/test_shared_db_go.py builds this binary and drives it, proving the Go
// and Python backends share the same DATABASE_URL. Exit 0 = phase passed.
//
// Usage: shared-db-interop <phase> <sqlalchemy-url>
//
// Phases:
//
//	seed-read   Python seeded the DB; Go migrates (must be a no-op), reads
//	            Python's rows through Go services, then writes its own.
//	go-verify   Go seeded the DB; Go re-reads what Python mutated in between.
package main

import (
	"fmt"
	"os"

	"wiwi-go/internal/auth"
	"wiwi-go/internal/store"
)

// Both sides hard-code these values, so each phase knows the other's rows.
const (
	pyUser       = "pyinterop"
	pyPassword   = "interop-pass-1"
	goUser       = "gointerop"
	goPassword   = "interop-pass-2"
	pyKeyAlias   = "py-seeded"
	goKeyAlias   = "go-minted"
	pyPriceModel = "gpt-4o-interop"
)

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: shared-db-interop <phase> <url>")
		os.Exit(2)
	}
	phase, url := os.Args[1], os.Args[2]
	s, err := store.Open(url)
	if err != nil {
		fail("open: %v", err)
	}
	defer s.Close()
	if err := s.Migrate(); err != nil {
		fail("migrate: %v", err)
	}
	svc := auth.NewService(s)
	switch phase {
	case "seed-read":
		seedRead(svc)
	case "go-verify":
		goVerify(svc)
	default:
		fail("unknown phase %q", phase)
	}
}

func seedRead(svc *auth.Service) {
	// 1) Migrate() ran against Python's schema: it must have been a no-op, so
	// a Python-created key must authenticate through Go's service.
	plain := mustEnv("WIWI_INTEROP_KEY")
	v, err := svc.Authenticate(plain)
	must(err, "authenticate python key")
	if v.Name != pyKeyAlias {
		fail("python key alias = %q, want %q", v.Name, pyKeyAlias)
	}
	if v.MaxBudget == nil || *v.MaxBudget != 5.0 {
		fail("python key max_budget = %v, want 5.0", v.MaxBudget)
	}

	// 2) A user Python created (pbkdf2_sha256 hex, 200k iters) must log in
	// through Go's verifier.
	u, err := svc.AuthenticateUser(pyUser, pyPassword)
	must(err, "authenticate python user")
	if u.Username != pyUser {
		fail("python user username = %q", u.Username)
	}

	// 3) A key owned by the Python user authenticates; Python disables that
	// user between this phase and go-verify, which must then refuse the key.
	ownedPlain := mustEnv("WIWI_INTEROP_OWNED_KEY")
	if _, err := svc.Authenticate(ownedPlain); err != nil {
		fail("owned key does not authenticate: %v", err)
	}

	// 4) Go writes: a user in Python's hash format and an un-owned key.
	// Python reads both back in its phase 2 with its own services.
	if _, err := svc.CreateUser(goUser, goPassword, "user"); err != nil {
		fail("create go user: %v", err)
	}
	gp, gv, err := svc.GenerateOwned("", goKeyAlias, new(10.0), nil, nil,
		[]string{pyPriceModel}, 0, 0)
	must(err, "generate key")
	fmt.Printf("GO_KEY_PLAINTEXT=%s\n", gp)
	fmt.Printf("GO_KEY_ID=%s\n", gv.ID)

	// 5) Prices Python wrote must be readable with Python's column names.
	var inCost, outCost float64
	if err := svc.Store().QueryRow(svc.Store().Rebind(
		`SELECT input_cost_per_token,output_cost_per_token FROM model_prices WHERE model_id=?`),
		pyPriceModel).Scan(&inCost, &outCost); err != nil {
		fail("read python price: %v", err)
	}
	if inCost != 1.5e-6 || outCost != 6e-6 {
		fail("python price = (%v,%v), want (1.5e-06,6e-06)", inCost, outCost)
	}
}

func goVerify(svc *auth.Service) {
	// Python disabled the shared user in its phase 2; Go must now refuse the
	// key that user owns (fail closed, like Python's _lookup_db).
	ownedPlain := mustEnv("WIWI_INTEROP_OWNED_KEY")
	if _, err := svc.Authenticate(ownedPlain); err == nil {
		fail("owned key still authenticates after owner disabled")
	}
	// Python bumped the Go-minted key's spend through update_spend; Go must
	// observe the new value through its own read path.
	keyID := mustEnv("WIWI_INTEROP_KEY_ID")
	row := svc.Store().QueryRow(svc.Store().Rebind(
		`SELECT spend_to_date FROM vkeys WHERE id=?`), keyID)
	var spendv float64
	if err := row.Scan(&spendv); err != nil {
		fail("read go key after python spend: %v", err)
	}
	if spendv < 1.0 {
		fail("go key spend = %v, want >= 1.0 (python update_spend not visible)", spendv)
	}
}

func mustEnv(name string) string {
	v := os.Getenv(name)
	if v == "" {
		fail("missing %s", name)
	}
	return v
}

func must(err error, what string) {
	if err != nil {
		fail("%s: %v", what, err)
	}
}

func fail(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "shared-db-interop: "+format+"\n", args...)
	os.Exit(1)
}
