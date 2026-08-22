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
  healthy: boolean;
  keys: PoolKey[];
}

export interface DeploymentInfo {
  provider: string;
  model_id: string;
  weight: number;
  available: boolean;
  inflight: number;
  p95_latency_ms: number;
  cooldown_remaining_s: number;
}

export interface ModelGroup {
  name: string;
  deployments: DeploymentInfo[];
}

export interface ModelsResponse {
  groups: ModelGroup[];
  aliases: Record<string, string>;
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
}

export interface Attempt {
  deployment: string;
  provider: string;
  key: string;
  status: string;
  latency_ms: number;
}

/** public_dict(LogEvent) for the request stream */
export interface RequestLogEntry {
  stream: "request";
  ts: number;
  request_id: string;
  surface: string;
  key_alias: string;
  model_group: string;
  provider: string;
  provider_key_label: string;
  status: number;
  error_code: string;
  tok_in: number;
  tok_cached: number;
  tok_reasoning: number;
  tok_out: number;
  tps: number;
  ttft_ms: number;
  latency_ms: number;
  cost: number;
  was_stream: boolean;
  cache_hit: boolean;
  cache_savings: number;
  attempts: Attempt[];
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
}

export interface TimeseriesResponse {
  bucket_seconds: number;
  metric: TimeseriesMetric;
  buckets: TokenBucket[] | TpsBucket[];
}

export interface AlertRule {
  id: string;
  name?: string;
  webhook_url: string;
  metric: "spend" | "error_rate" | "requests_per_minute";
  threshold: number;
}
