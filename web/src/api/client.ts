// Fetch wrapper: bearer auth from localStorage, JSON errors as ApiError.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

const TOKEN_KEY = "wiwi.master_key";

export function getToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${getToken()}`);
  if (init?.body != null && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  let resp: Response;
  try {
    resp = await fetch(path, { ...init, headers });
  } catch (e) {
    throw new ApiError(0, `network error: ${String(e)}`);
  }
  if (resp.status === 401) {
    throw new ApiError(401, "master key required or invalid");
  }
  const text = await resp.text();
  if (!resp.ok) {
    let msg = text;
    try {
      const body = JSON.parse(text) as { error?: { message?: string } };
      msg = body.error?.message ?? text;
    } catch {
      // plain-text error body
    }
    throw new ApiError(resp.status, msg || `HTTP ${resp.status}`);
  }
  return (text ? JSON.parse(text) : null) as T;
}

// -- typed endpoint helpers --------------------------------------------------

import type {
  AlertRule,
  BuiltinProvider,
  DeploymentInfo,
  ModelPrice,
  ModelsResponse,
  OverviewStats,
  PoolKey,
  Provider,
  ProxyLogEntry,
  RequestLogEntry,
  TimeseriesMetric,
  TimeseriesResponse,
  UpstreamModel,
  VirtualKey,
} from "./types";

export const getProviders = () =>
  api<{ providers: Provider[] }>("/admin/providers");

export const getBuiltinProviders = () =>
  api<{ providers: BuiltinProvider[] }>("/admin/provider-catalog");

export const patchProviderKey = (
  provider: string,
  label: string,
  patch: { enabled?: boolean; weight?: number },
) =>
  api<{ key: PoolKey }>(`/admin/providers/${encodeURIComponent(provider)}/keys/${encodeURIComponent(label)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const addProviderKey = (
  provider: string,
  body: { label: string; key: string; weight?: number },
) =>
  api<{ key: PoolKey }>(`/admin/providers/${encodeURIComponent(provider)}/keys`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const addProvider = (body: {
  name: string;
  provider_type: string;
  base_url?: string;
  label?: string;
  key: string;
}) => api<{ name: string }>("/admin/providers", { method: "POST", body: JSON.stringify(body) });

export const deleteProvider = (name: string) =>
  api<{ deleted: boolean; name: string }>(
    `/admin/providers/${encodeURIComponent(name)}`,
    { method: "DELETE" },
  );

export const patchProvider = (
  name: string,
  patch: { name?: string; base_url?: string; provider_type?: string; round_robin?: boolean },
) =>
  api<Provider>(`/admin/providers/${encodeURIComponent(name)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const deleteProviderKey = (provider: string, label: string) =>
  api<{ deleted: boolean; label: string }>(
    `/admin/providers/${encodeURIComponent(provider)}/keys/${encodeURIComponent(label)}`,
    { method: "DELETE" },
  );

export const getModels = () => api<ModelsResponse>("/admin/models");

export const patchModelGroup = (
  group: string,
  patch: { weights?: Record<string, number>; strategy?: string },
) =>
  api<{ group: string }>(`/admin/model-groups/${encodeURIComponent(group)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const fetchProviderModels = (provider: string) =>
  api<{ models: UpstreamModel[] }>(
    `/admin/providers/${encodeURIComponent(provider)}/models`,
  );

export const addDeployment = (
  group: string,
  body: { provider: string; model_id: string; weight?: number },
) =>
  api<{ deployment: DeploymentInfo }>(
    `/admin/model-groups/${encodeURIComponent(group)}/deployments`,
    { method: "POST", body: JSON.stringify(body) },
  );

export const listKeys = () => api<{ keys: VirtualKey[] }>("/admin/keys");

export const generateKey = (body: {
  name: string;
  custom_key?: string;
  models?: string[];
  max_budget?: number | null;
  rpm?: number | null;
  tpm?: number | null;
  ttl_seconds?: number | null;
}) =>
  api<{ key: string; id: string; note: string }>("/admin/keys/generate", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const patchKey = (
  id: string,
  patch: {
    max_budget?: number | null;
    rpm?: number | null;
    tpm?: number | null;
    models?: string[];
    expires_at?: number | null;
  },
) =>
  api<{ key: VirtualKey }>(`/admin/keys/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });

export const disableKey = (id: string, disabled: boolean) =>
  api<{ key_id: string; disabled: boolean }>(`/admin/keys/${encodeURIComponent(id)}/disable`, {
    method: "POST",
    body: JSON.stringify({ disabled }),
  });

export const deleteKey = (id: string) =>
  api<{ deleted: boolean }>(`/admin/keys/${encodeURIComponent(id)}`, { method: "DELETE" });

export const getOverview = (minutes: number) =>
  api<OverviewStats>(`/admin/stats/overview?minutes=${minutes}`);

export const getTimeseries = (metric: TimeseriesMetric, minutes: number) =>
  api<TimeseriesResponse>(
    `/admin/stats/timeseries?bucket=minute&metric=${metric}&minutes=${minutes}`,
  );

export const getRequestLogs = () =>
  api<{ logs: RequestLogEntry[] }>("/admin/logs/requests?limit=10000");

export const getRequestLogsWithLimit = (limit: number) =>
  api<{ logs: RequestLogEntry[] }>(`/admin/logs/requests?limit=${limit}`);

export const getPricing = () =>
  api<{ models: ModelPrice[] }>("/admin/pricing");

export const upsertPricing = (
  modelId: string,
  body: {
    input_per_1m: number;
    output_per_1m: number;
    cache_read_per_1m?: number;
    max_input_tokens?: number;
    max_output_tokens?: number;
    mode?: string;
  },
) =>
  api<ModelPrice>(`/admin/pricing/${encodeURIComponent(modelId)}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const deletePricing = (modelId: string) =>
  api<{ deleted: boolean; model_id: string }>(
    `/admin/pricing/${encodeURIComponent(modelId)}`,
    { method: "DELETE" },
  );

export const getProxyLogs = () =>
  api<{ logs: ProxyLogEntry[] }>("/admin/logs/proxy");

export const getAlertRules = () => api<{ rules: AlertRule[] }>("/admin/alert-rules");

export const putAlertRules = (rules: AlertRule[]) =>
  api<{ rules: AlertRule[] }>("/admin/alert-rules", {
    method: "PUT",
    body: JSON.stringify({ rules }),
  });
