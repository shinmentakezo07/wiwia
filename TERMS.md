# wiwi — Terms of Use

The **code** of wiwi is MIT licensed (see [LICENSE](LICENSE)) — that part is
not changing. These terms govern **operating a wiwi server** and set
expectations for everyone who uses the software. If you have a question about
how these terms apply to you, open an issue or contact the maintainer.

## 1. Personal use — free

Running wiwi for yourself, your research, your homelab, or inside your own
company for internal traffic costs nothing. No fee, no registration, no
attribution required.

## 2. Commercial operation — conditions apply

If you **charge money** for access to a wiwi-based service — a hosted
gateway, an API resell, a product where wiwi serves paying customers'
traffic — you accept these conditions by doing so:

- **No impersonation.** Don't present the service as official, endorsed by,
  or affiliated with wiwi or its maintainer unless you have written
  permission.
- **No fraud or deception.** The service must not be used to defraud end
  users — including misrepresenting which model or provider serves their
  requests, silently downgrading models, or faking usage and billing data.
- **Honor upstream terms.** You are responsible for holding valid API keys
  and complying with every provider's terms of service whose traffic you
  route. Routing around a provider's restrictions is on you, not wiwi.
- **Keep the license intact.** The MIT license and this notice stay with the
  software; don't relicense derived distributions under terms that forbid
  what MIT allows.
- **Be reachable.** Publish a working contact (email or issue tracker) for
  abuse reports on your deployment.

Commercial use is **permitted**, not paid — these are the conditions, not an
invoice. If you'd like a co-marketing mention or have questions, reach out.

## 3. Acceptable use — no fraud, no abuse

Regardless of commercial status, you agree **not** to use wiwi — the code,
a self-hosted instance, or a hosted deployment — to:

- defraud, deceive, or impersonate people or organizations;
- launder API access, resell stolen or shared provider keys, or evade
  provider bans and rate limits;
- generate illegal content, or process data in violation of privacy laws
  (GDPR, CCPA, and similar) applicable to you;
- attack third parties (the gateway is a proxy — its keys and quotas are
  yours to keep secured).

## 4. No liability

wiwi is provided **"as is", without warranty of any kind**, to the maximum
extent permitted by law — this mirrors the MIT license and states it plainly:

- The maintainer is **not responsible** for how you, your users, or any
  third party use this codebase or any server built from it — including
  fraud, losses, or damage to third parties caused by misuse.
- The maintainer is **not responsible** for provider account suspensions,
  API bans, billing surprises, or data loss arising from running wiwi.
- **You** are solely responsible for your deployment: its security, its
  keys, its traffic, its compliance with the laws of your jurisdiction, and
  the conduct of anyone using your instance.

If someone runs a wiwi server and commits fraud with it, that is between
them and their victims — not the software, and not its author.

---

*These terms do not restrict the MIT-licensed code itself. Where they
conflict with [LICENSE](LICENSE), LICENSE governs the code and these terms
govern conduct.*
