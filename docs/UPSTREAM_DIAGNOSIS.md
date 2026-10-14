# Diagnosing an upstream refusal

Playbook for the class of problem that costs the most time: **an upstream starts
refusing requests and the error does not say why.** The provider might be wrong
about the credential, might be refusing on policy, or might simply not
recognise what we are sending. The three have completely different fixes, and
the error text usually points at the wrong one.

Read this before theorising about any `4xx` from a provider. The worked example
is [OPENCODE.md](OPENCODE.md), where a free tier refused everything for a week
and the message blamed billing throughout.

- [Step 0 — the error text is the weakest evidence](#step-0--the-error-text-is-the-weakest-evidence)
- [Three causes, one decisive test](#three-causes-one-decisive-test)
- [The method](#the-method)
- [Traps](#traps)
- [Where the evidence goes](#where-the-evidence-goes)

---

## Step 0 — the error text is the weakest evidence

Read the error as *data*, not as a diagnosis. Three layers sit between the
upstream's words and the string a client finally sees, and any of them can
relabel the cause:

| layer | what it does | example |
|---|---|---|
| upstream | decides and picks a type | `FreeTierError` |
| `error_from_provider_status()` | maps status+type → wiwi `WiwiError` | `FreeTierError` + 403 → `402 permission_error`, message `"<provider> requires billing (403)"` |
| wire encoder | renders it in the caller's dialect | a Claude Code-style "API Error" line |

In the worked example the upstream said *"this request does not look like our
client"*, wiwi relabelled it **"requires billing"**, and that label is what sent
the investigation — mine included — after keys, accounts and quotas for a round.
The message was accurate about the *class* of refusal and misleading about the
*cause*.

**So: before forming a hypothesis, get the upstream's raw body.** Log the status
and the un-rewritten error `type` at the point of refusal. One line of evidence
beats an hour of reading the final message.

---

## Three causes, one decisive test

| | credential | entitlement / policy | request shape |
|---|---|---|---|
| meaning | wrong/expired key | the account may not do this | we are not who/what we claim to be |
| typical types | `AuthError`, `Invalid API key`, `401` | `CreditsError`, `RegionError`, `ModelError`, `DataPolicyError`, `FreeTierError` | same types, reused as shape refusals |
| **does a different key change it?** | **yes** | no | no |
| **does the real client work?** | no | no | **yes** |
| fix | fix the key | add credits / change region / drop the model | change what we send |

Two questions separate all three, in this order:

1. **Does the request work with a different credential of the same account?** If
   yes → shape or entitlement, never credential.
2. **Does the vendor's own client work, right now, from this machine, on the
   same model?** If yes → shape. If no → credential, account, network, or the
   offer itself.

Question 2 is the one that gets skipped and it is the one that carries the
diagnosis. See [Traps](#traps) for how to ask it so the answer means something.

---

## The method

### 1. Reproduce minimally

One request, one model, no gateway. Get the smallest thing that fails: a `curl`
or a 15-line script using the adapter directly. A failure you cannot reproduce
in isolation is not yet a fact.

### 2. Isolate the control **before** believing it

Any comparison you draw ("the CLI does it too", "it worked last week") must run
in an environment that shares nothing with the failing path. If your control
runs through a proxy, a config override, a shim, a different base URL, or a
cached session that the failing path does not have — **it is not a control**, it
is a second experiment wearing a label.

### 3. Capture the known-good request

When a vendor ships a first-party client, that client is the reference
implementation. Put a logging proxy in front of the vendor endpoint (via the
client's provider/base-URL override, not a system-wide MITM) and dump the exact
headers and body it sends. Write the bytes to a file — you will replay them.

### 4. Replay the bytes verbatim

Send those exact bytes straight to the upstream.

- **Replays 200** → the difference is in the HTTP request. Continue.
- **Replay fails** → the difference is *not* in the HTTP request. Stop looking
  at headers and bodies: consider TLS/client fingerprinting, a session or
  cookie established out of band, a pre-flight registration call, or an
  upstream-side account state. This step exists to save hours of bisecting a
  request that was never the problem.

### 5. Bisect one factor at a time

From the known-good request, remove one thing and re-send. Sweep headers and
body **separately**, and make sure each probe's body is itself otherwise valid —
a minimal probe body can fail for an unrelated reason and make a header look
irrelevant when it is load-bearing.

Every row is `change → result`. Keep the table; it is the deliverable.

### 6. Rebuild from the minimum and verify each layer

Assemble the smallest request that passes. Then add back *our* extras one at a
time and confirm each is either load-bearing or harmless. Headers we invented
"just in case" are a common source of new refusals — remove what is not proven.

### 7. Fix the code, then fix the claims

Update the adapter. Then **correct every statement that is now false** —
docstrings, config comments, example configs, `AUDIT.md`, `UPDATE.md`, tests
that pinned the old behaviour. A stale comment is how the next person
re-diagnoses the same bug correctly-looking.

---

## Traps

Each of these produced a wrong answer in the worked example.

**A control that shares infrastructure with the failing path.** A test run of the
real client was launched from a scratch directory whose config overrode the
provider base URL to a local proxy. It "confirmed" the feature was dead. Run
clean, the same command worked. *A control sharing infrastructure with the thing
under test proves nothing — re-run it in isolation before building on it.*

**Concluding "upstream closed it" from one failing probe.** Refusal can be
transient (quota, rate limit, rollout, region, session bucket). "It fails now"
is not "it is gone". Require a known-good client failing *cleanly* before
writing down that an offer was withdrawn.

**Bisecting with a body that fails for another reason.** Early probes used a
minimal body; every header variation then "didn't matter", because the body was
already being refused. Each probe must differ from the **passing** row by exactly
one factor, or it proves nothing about the factor you changed.

**Treating a reused identifier as proof.** A stale session, a cached token or a
long-lived cookie can hold a refusal in place, and can equally hide a fix.
Establish a clean baseline for both the passing and the failing case.

**Inferring capability from a name.** A header or field called `auth`/`key`/`id`
proves nothing about what the edge validates. The gate may read presence, may
read a shape, and may read neither. Measure it.

**Blaming the newest thing.** When a vendor updates, their gate usually moves
with it and our client is what is stale. Check whether *their* current client
works before theorising about our code at all.

---

## Where the evidence goes

A refusal investigation is only finished when the next person can skip it:

- **A matrix in the provider's doc or `AUDIT.md`** — the `change → result` rows,
  including the ones that surprised you. A future agent who sees
  "six inert tools 403, six real tools renamed 403" does not have to rediscover
  it.
- **The method alongside it**, so it can be re-run when the client moves again.
- **A code comment on each non-obvious condition**, stating the evidence rather
  than asserting the conclusion.
- **Corrections, not appendices.** If an earlier entry was wrong, edit it and say
  what was wrong and why the correction is better supported — do not leave a
  false claim in the file with a newer true one beneath it.

Record `probed` vs `documented` for every claim. An untested table transcribed
from a vendor page is a hypothesis wearing a table's clothes, and must be
labelled as one.

---

## Quick reference

```
error from upstream
  │
  ├─ get the RAW type, not the rendered message          ← do this first
  │
  ├─ same request, different credential?
  │     yes → credential problem
  │     no  ↓
  │
  ├─ vendor's own client, clean environment, same model, same machine?
  │     no  → account / offer / network — not our request
  │     yes ↓
  │
  ├─ capture → replay
  │     replay fails  → outside HTTP (TLS, session, preflight, account state)
  │     replay 200    → bisect one factor at a time from the passing row
  │
  └─ rebuild minimal → add our extras → verify → fix code → correct the docs
```
