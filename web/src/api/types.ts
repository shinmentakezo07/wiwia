// Types mirroring the wiwi admin API payloads (see wiwi/server/app.py).

export interface PoolKey {
  label: string;
  masked: string;
  weight: number;
  enabled: boolean;
  status: "active" | "cooling" | "invalid" | "disabled";
  cooldown_remaining_s: number;
  req_count: number;
  err_count: number;
  last_used_ts: number | null;
}

export interface Provider {
  name: string;
  provider_type: string;
  base_url: string;
  round_robin: boolean;
  alias_id: string | null;
  healthy: boolean;
  keys: PoolKey[];
}

export interface DeploymentInfo {
  provider: string;
  model_id: string;
  weight: number;
  available: boolean;
  /** Healer-restored; skipped while a non-probation sibling is available. */
  probation?: boolean;
  inflight: number;
  p95_latency_ms: number;
  cooldown_remaining_s: number;
}

export interface ModelGroup {
  name: string;
  deployments: DeploymentInfo[];
}

// Rich alias value (shinway-style ModelAliasEntry). Plain strings remain
// legal in alias maps and are treated as target-only (force_mapping default
// = true, no display name, no fork).
export interface ModelAliasEntry {
  target: string;
  force_mapping?: boolean;
  display_name?: string | null;
  fork?: boolean;
}

// True when the alias value is a rich entry rather than a plain string.
export function isModelAliasEntry(
  v: string | ModelAliasEntry | undefined | null,
): v is ModelAliasEntry {
  return typeof v === "object" && v !== null && "target" in v;
}

// Extract the target group name from a value that may be a string or a
// rich entry. Returns the value unchanged if neither.
export function aliasTarget(v: string | ModelAliasEntry | undefined | null): string {
  if (v == null) return "";
  if (isModelAliasEntry(v)) return v.target;
  return v;
}

// ForceMapping flag for a value; defaults to true for plain strings and
// when the rich form omits the field (matches wiwi's Pydantic default).
export function aliasForceMapping(v: string | ModelAliasEntry | undefined | null): boolean {
  if (isModelAliasEntry(v)) return v.force_mapping !== false;
  return true;
}

// Optional display name (rich entry only).
export function aliasDisplayName(v: string | ModelAliasEntry | undefined | null): string | null {
  return isModelAliasEntry(v) ? (v.display_name ?? null) : null;
}

export interface ModelsResponse {
  groups: ModelGroup[];
  aliases: Record<string, string | ModelAliasEntry>;
  provider_aliases: Record<string, string>;
  strategy: string;
}

export interface UpstreamModel {
  id: string;
}

export interface VirtualKey {
  id: string;
  alias: string;
  models: string[];
  max_budget: number | null;
  spend_to_date: number;
  rpm: number | null;
  tpm: number | null;
  expires_at: number | null;
  disabled: boolean;
  owner_id?: string | null;
  budget_reserved?: number;
  /** Concurrency lane this key is routed in (`RouterSettings.priority_lanes`).
   *  `null` = unset, which the router resolves to `default_lane`. */
  priority?: string | null;
}

export interface Attempt {
  deployment: string;
  provider: string;
  key: string;
  status: string;
  latency_ms: number;
}

/** Authoritative per-request metrics used by the Playground footer. */
export interface PlaygroundMetrics {
  request_id: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  tps: number;
  ttft_ms: number;
  latency_ms: number;
  usage_estimated: boolean;
}

/** public_dict(LogEvent) for the request stream */
export interface RequestLogEntry {
  stream: "request";
  ts: number;
  request_id: string;
  surface: string;
  key_alias: string;
  /** Virtual key id this request was billed to (empty for master-key traffic). */
  key_id: string;
  model_group: string;
  provider: string;
  provider_key_label: string;
  status: number;
  error_code: string;
  /** Exact upstream error message returned to the caller (empty on success). */
  error_message: string;
  tok_in: number;
  tok_cached: number;
  /** Cache-*creation* tokens, billed above the cached-read rate. Distinct from
   *  `tok_cached` — Anthropic reports both on one request. */
  tok_cache_creation: number;
  tok_reasoning: number;
  tok_out: number;
  /** True when usage was estimated rather than reported by the provider. */
  usage_estimated: boolean;
  tps: number;
  ttft_ms: number;
  latency_ms: number;
  cost: number;
  was_stream: boolean;
  /** Provider prompt-cache hit (feeds `wiwi_prompt_cache_hits_total`). */
  cache_hit: boolean;
  cache_savings: number;
  /** Served from wiwi's own exact-match response cache. NOT the same as
   *  `cache_hit` — a response-cache hit must leave `cache_hit` false. */
  response_cache_hit: boolean;
  /** Non-fatal notes from the dialect translation layer (dropped params,
   *  lossy re-encodes). Empty when the request round-tripped cleanly. */
  translation_warnings: string[];
  attempts: Attempt[];
  /** Raw request body (only when store_prompts_in_spend_logs is enabled). */
  request_body?: Record<string, unknown> | unknown[] | null;
  /** Model response snapshot (only when store_prompts_in_spend_logs is enabled). */
  response_body?: {
    text: string;
    thinking?: { text: string }[];
    tool_calls?: { id: string; name: string; arguments: string | Record<string, unknown> }[];
    stop_reason?: string;
    usage?: {
      prompt_tokens: number;
      completion_tokens: number;
      cached_tokens: number;
      reasoning_tokens: number;
    } | null;
    response?: unknown;
    streamed?: boolean;
  } | string | null;
}

export interface ProxyLogEntry {
  stream: "proxy";
  ts: number;
  level: "debug" | "info" | "warn" | "error";
  message: string;
  request_id?: string;
}

export interface OverviewStats {
  window_minutes: number;
  generated_at: number;
  requests: number;
  errors: number;
  error_rate: number;
  requests_per_minute: number;
  tok_in: number;
  tok_cached: number;
  tok_reasoning: number;
  tok_out: number;
  cache_hits: number;
  cache_hit_rate: number;
  tps_avg: number;
  tps_p95: number;
  /** Share of requests in the window carrying a measurable TPS sample. TPS is
   *  only recorded for streaming requests, so this is how much traffic the
   *  tps figures actually describe. */
  tps_sample_ratio: number;
  ttft_p95_ms: number;
  latency_p95_ms: number;
  cost: number;
  cache_savings: number;
}

export type TimeseriesMetric = "tokens" | "tps";

export interface TokenBucket {
  t: number;
  tok_in: number;
  tok_cached: number;
  tok_reasoning: number;
  tok_out: number;
}

export interface TpsBucket {
  t: number;
  tps_avg: number;
  tps_p95: number;
  /** Share of requests in this bucket carrying a measurable TPS sample — TPS
   *  is only recorded for streaming requests. */
  tps_sample_ratio: number;
}

export interface TimeseriesResponse {
  bucket_seconds: number;
  metric: TimeseriesMetric;
  buckets: TokenBucket[] | TpsBucket[];
}

/** A price bound to one provider — an account name or a provider type.
 *  `provider` is omitted on a model's base (all-providers) entry. */
export interface ModelPriceScope {
  provider: string;
  input_per_1m: number;
  output_per_1m: number;
  cache_read_per_1m?: number;
  cache_creation_per_1m?: number;
}

export interface ModelPrice {
  model_id: string;
  input_per_1m: number;
  output_per_1m: number;
  cache_read_per_1m?: number;
  cache_creation_per_1m?: number;
  max_input_tokens?: number;
  max_output_tokens?: number;
  mode?: string;
  /** Per-provider overrides. Absent when the model has only a base rate. */
  scopes?: ModelPriceScope[];
}

export interface AlertRule {
  id: string;
  name?: string;
  webhook_url: string;
  metric: "spend" | "error_rate" | "requests_per_minute";
  threshold: number;
}

/** Authenticated console user (from /auth/me, /auth/signup, /auth/login). */
export interface User {
  id: string;
  username: string;
  role: "user" | "admin";
}

/** Public, secret-free model catalog entry (from /public/models). */
export interface PublicModelGroup {
  name: string;
  deployments: { provider: string; model_id: string }[];
}

/** A built-in provider type that ships with wiwi (from /admin/provider-catalog). */
export interface BuiltinProvider {
  provider_type: string;
  label: string;
  default_base_url: string;
  description: string;
  /** Latest model IDs from the provider's current catalog */
  latest_models: string[];
  /** Advertised context window */
  context_window: string;
  /** Link to the provider's official API docs */
  docs_url: string;
  builtin: true;
  /** true if at least one configured account uses this provider type */
  configured: boolean;
}

// -- Cline OAuth -------------------------------------------------------------

export interface ClineLoginUrlResponse {
  auth_url: string;
}

export interface ClineConnectResponse {
  provider: string;
  email: string | null;
  access_token_masked: string;
}

export interface ClineStatusResponse {
  connected: boolean;
  email?: string | null;
  expires_at?: string | null;
  needs_refresh?: boolean;
}

export interface ClineRefreshResponse {
  provider: string;
  access_token_masked: string;
  expires_at: string | null;
}
// -- Hermes (Nous Portal) OAuth (device-code flow) ---------------------------
export interface HermesDeviceCodeResponse {
  state: string;
  provider: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete: string;
  expires_in: number;
  interval: number;
}
export interface HermesPollResponse {
  pending?: boolean;
  provider?: string;
  connected?: boolean;
  access_token_masked?: string;
  expires_at?: number | null;
}
/** One pool key's Hermes (Nous Portal) account state. A wiwi provider pool can
 *  hold several Nous identities (one per key, AUDIT #427), so the status route
 *  reports a list rather than a single flat account. ``managed: false`` means
 *  the key holds a raw Nous API key / bare token: usable for inference but
 *  never auto-refreshable. ``expires_at`` is epoch *seconds* (nullable when the
 *  credential carries no known expiry). */
export interface HermesAccountStatus {
  key_label: string;
  managed: boolean;
  /** Nous account email, when the OAuth credential recorded one. */
  label?: string | null;
  expires_at?: number | null;
  /** True when the token is expired or within the refresh lead window. */
  needs_refresh?: boolean;
  has_refresh_token?: boolean;
}
export interface HermesStatusResponse {
  /** True when at least one account in the pool is an OAuth-managed account. */
  connected: boolean;
  accounts?: HermesAccountStatus[];
}
export interface HermesRefreshResponse {
  provider: string;
  /** The pool key that was actually refreshed (server picks the first managed
   *  one when the request omits a ``key_label``). */
  key_label?: string;
  access_token_masked: string;
  expires_at: number | null;
}
export interface HermesDisconnectResponse {
  provider: string;
  /** How many managed accounts were removed (0 for the whole pool when nothing
   *  was OAuth-managed). */
  disconnected: number;
}

export interface ClineDisconnectResponse {
  provider: string;
  disconnected: boolean;
}

// -- WorkBuddy accounts ------------------------------------------------------

export interface WorkBuddyAccount {
  provider: string;
  label: string;
  status: string;
  enabled: boolean;
  valid_auth: boolean;
  uid?: string;
  nickname?: string;
  domain?: string;
  region?: "cn" | "global";
  expires_at?: number | null;
  needs_refresh?: boolean;
  has_refresh_token?: boolean;
  access_token_masked?: string;
}

export interface WorkBuddyAccountsResponse {
  accounts: WorkBuddyAccount[];
}

export interface WorkBuddyImportResponse {
  provider: string;
  imported: number;
  labels: string[];
}

export interface WorkBuddyExportResponse {
  accounts: WorkBuddyAuthFile[];
}

/** One account in the workbuddy2api auths/ file shape. */
export interface WorkBuddyAuthFile {
  auth: {
    accessToken: string;
    refreshToken: string;
    expiresAt: number;
    domain: string;
  };
  account: {
    uid: string;
    enterpriseId: string;
    nickname: string;
  };
}

export interface WorkBuddyRefreshResponse {
  provider: string;
  label: string;
  refreshed: boolean;
}

// -- Provider backup (export/import) -------------------------------------------

export interface ProviderBackupKey {
  label: string;
  secret: string;
  weight: number;
  enabled: boolean;
}

export interface ProviderBackupDeployment {
  group_name: string;
  model_id: string;
  weight: number;
}

export interface ProviderBackup {
  name: string;
  provider_type: string;
  base_url?: string;
  timeout_s?: number;
  extra_headers?: Record<string, string>;
  round_robin?: boolean;
  alias_id?: string | null;
  keys: ProviderBackupKey[];
  deployments?: ProviderBackupDeployment[];
}

export interface ProviderExportResponse {
  version: number;
  exported_at: number;
  providers: ProviderBackup[];
}

export interface ProviderImportResponse {
  imported_providers: number;
  imported_keys: number;
  imported_deployments: number;
  providers: string[];
}

// -- Whole-gateway backup / restore --------------------------------------------
//
// Mirrors `wiwi/server/backup.py`. Sections are the unit of both the export
// filter and the import filter; each upserts on its own natural key and none
// of them ever deletes.

export type BackupSections =
  | "providers"
  | "keys"
  | "deployments"
  | "aliases"
  | "settings"
  | "prices"
  | "users"
  | "vkeys"
  | "logs"
  | "rollups"
  | "audit"
  | "responses";

export interface BackupProvider {
  name: string;
  provider_type: string;
  base_url: string;
  timeout_s?: number | null;
  extra_headers?: Record<string, string> | null;
  round_robin?: boolean | null;
  alias_id?: string | null;
}

export interface BackupKey {
  provider_name: string;
  label: string;
  /** Absent — not null — when the export ran without `include_secrets`. That
   *  distinction is what lets a merge leave an existing secret alone instead
   *  of blanking it. */
  secret?: string;
  weight?: number;
  enabled?: boolean;
}

export interface BackupDeployment {
  group_name: string;
  provider_name: string;
  model_id: string;
  weight?: number;
  rpm?: number | null;
  tpm?: number | null;
  timeout?: number | null;
  max_tokens?: number | null;
  prompt_cache?: boolean;
  /** The source recorded this deployment as detached. Applying it is that
   *  same removal — not a wipe. */
  detached?: boolean;
}

/** `null` is a tombstone: an admin alias that removed a YAML-defined one. */
export type BackupAliasValue = string | { target: string; [k: string]: unknown } | null;

export interface BackupPrice {
  model_id: string;
  input_cost_per_token?: number | null;
  output_cost_per_token?: number | null;
  cache_read_input_cost_per_token?: number | null;
  cache_creation_input_cost_per_token?: number | null;
  max_input_tokens?: number | null;
  max_output_tokens?: number | null;
  mode?: string | null;
  providers?: Record<string, Record<string, number | null>>;
}

export interface BackupUser {
  id?: string;
  username: string;
  /** PBKDF2 digest, carried through verbatim. Opaque — never displayed. */
  password_hash?: string | null;
  role?: string;
  disabled?: boolean;
}

/** A virtual key. `key_hash` is SHA-256 of a plaintext that is never stored,
 *  so importing it restores a key that still authenticates — but no key can be
 *  re-displayed after a restore. Merge key is `key_hash`. */
export interface BackupVKey {
  id?: string;
  key_hash: string;
  key_alias?: string;
  models?: string[] | null;
  max_budget?: number | null;
  spend_to_date?: number;
  budget_reserved?: number;
  rpm?: number | null;
  tpm?: number | null;
  expires_at?: number | null;
  disabled?: boolean;
  owner_id?: string | null;
  priority?: string | null;
}

export interface BackupLogRow {
  request_id: string;
  ts?: number;
  [k: string]: unknown;
}

export interface BackupDocument {
  kind: "wiwi.backup";
  version: number;
  exported_at: number;
  sections: BackupSections[];
  include_secrets: boolean;
  include_bodies: boolean;
  truncated?: Record<string, number>;
  counts: Record<string, number | Record<string, number>>;
  providers?: BackupProvider[];
  keys?: BackupKey[];
  deployments?: BackupDeployment[];
  aliases?: Record<string, BackupAliasValue>;
  settings?: Record<string, unknown>;
  prices?: BackupPrice[];
  users?: BackupUser[];
  vkeys?: BackupVKey[];
  logs?: { request_logs: BackupLogRow[] };
  rollups?: { request_rollups: Record<string, unknown>[] };
  audit?: { audit_logs: Record<string, unknown>[] };
  responses?: { stored_responses: Record<string, unknown>[] };
}

/** What `POST /admin/backup/import?dry_run=1` returns: the per-section row
 *  counts the merge would apply, with nothing written. */
export interface BackupPlan {
  dry_run: true;
  sections: BackupSections[];
  counts: Record<string, number | Record<string, number>>;
}

/** A bulk section's merge outcome.
 *
 *  Every row in these sections runs in its own SAVEPOINT, so one
 *  un-insertable row rolls back alone instead of taking the section — and is
 *  then *counted* rather than swallowed. `failed > 0` means the file carried
 *  rows this gateway refused and they were never written, so the console has
 *  to report it: a merged count alone would read as "everything restored".
 *  Mirrors `_apply_rollups` / `_apply_responses` in `wiwi/server/backup.py`. */
export interface BackupSectionResult {
  written: number;
  failed: number;
}

export interface BackupImportResult {
  providers: number;
  keys: number;
  deployments: number;
  aliases: number;
  settings: number;
  prices: number;
  users: number;
  vkeys: number;
  /** Raw log rows, applied as one bulk page-insert: all or nothing, so a plain
   *  count. */
  logs: number;
  rollups: BackupSectionResult;
  /** Audit rows the file carried that were deliberately NOT replayed. */
  audit_skipped: number;
  responses: BackupSectionResult;
}
