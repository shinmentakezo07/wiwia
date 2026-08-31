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
    // credentials:"include" carries the HttpOnly session cookie alongside the
    // bearer header (kept for master-key admin back-compat). For username/
    // password users the token is empty → "Bearer " header is harmless and the
    // server falls back to the cookie.
    resp = await fetch(path, { ...init, headers, credentials: "include" });
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
  ClineAutoConnectResponse,
  ClineConnectResponse,
  ClineDisconnectResponse,
  ClineLoginUrlResponse,
  ClineRefreshResponse,
  ClineStatusResponse,
  DeploymentInfo,
  ModelPrice,
  ModelsResponse,
  OverviewStats,
  PoolKey,
  Provider,
  ProxyLogEntry,
  PublicModelGroup,
  RequestLogEntry,
  TimeseriesMetric,
  TimeseriesResponse,
  UpstreamModel,
  WorkBuddyAccountsResponse,
  WorkBuddyAuthFile,
  WorkBuddyExportResponse,
  WorkBuddyImportResponse,
  WorkBuddyRefreshResponse,
  User,
  VirtualKey,
} from "./types";

export interface ProvidersResponse {
  providers: Provider[];
  alias_to_provider: Record<string, string>;
}
export const getProviders = () =>
  api<ProvidersResponse>("/admin/providers");

export const getBuiltinProviders = () =>
  api<BuiltinProvider[]>("/admin/provider-catalog");

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

export const revealProviderKey = (provider: string, label: string) =>
  api<{ label: string; secret: string }>(
    `/admin/providers/${encodeURIComponent(provider)}/keys/${encodeURIComponent(label)}/secret`,
    { method: "GET" },
  );

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
  patch: { name?: string; base_url?: string; provider_type?: string;
            round_robin?: boolean; alias_id?: string | null },
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

export const fetchClineModels = () =>
  api<{ models: UpstreamModel[] }>("/admin/cline/models");

// Global Cline default-model settings — a list of model ids that should
// be auto-deployed to every Cline account. Persists across restarts and
// auto-applies to providers added at runtime. See backend
// ``/admin/cline/settings`` and tests/test_cline_global_model.py.
export interface ClineSettingsResponse {
  default_models: string[];
}
export const getClineSettings = () =>
  api<ClineSettingsResponse>("/admin/cline/settings");
export const putClineSettings = (default_models: string[]) =>
  api<ClineSettingsResponse>("/admin/cline/settings", {
    method: "PUT",
    body: JSON.stringify({ default_models }),
  });
export const deleteClineDefaultModel = (modelId: string) =>
  api<{ default_models: string[] }>(
    `/admin/cline/settings/default-models/${encodeURIComponent(modelId)}`,
    { method: "DELETE" },
  );

export const addDeployment = (
  group: string,
  body: { provider: string; model_id: string; weight?: number },
) =>
  api<{ deployment: DeploymentInfo }>(
    `/admin/model-groups/${encodeURIComponent(group)}/deployments`,
    { method: "POST", body: JSON.stringify(body) },
  );

// provider/model_id travel as query params, not path segments: model ids
// contain slashes (e.g. z-ai/glm-5.2) that a path param would mangle.
export const deleteDeployment = (group: string, provider: string, modelId: string) =>
  api<{ deleted: boolean; group: string; provider: string; model_id: string;
         group_emptied: boolean }>(
    `/admin/model-groups/${encodeURIComponent(group)}/deployments`
    + `?provider=${encodeURIComponent(provider)}`
    + `&model_id=${encodeURIComponent(modelId)}`,
    { method: "DELETE" },
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

// -- session auth + user + public catalog helpers ----------------------------

export const getMe = () => api<{ user: User | null }>("/auth/me");

export const signupUser = (body: { username: string; password: string }) =>
  api<{ user: User; playground_key?: string }>("/auth/signup", {
    method: "POST",
    body: JSON.stringify(body),
    credentials: "include",
  });

export const loginUser = (body: { username: string; password: string }) =>
  api<{ user: User; playground_key?: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
    credentials: "include",
  });

export const loginMaster = (body: { master_key: string }) =>
  api<{ user: User; playground_key?: string }>("/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
    credentials: "include",
  });

export const logoutSession = () =>
  api<{ ok: true }>("/auth/logout", { method: "POST", credentials: "include" });

/** Mint a fresh playground key for the current session (fallback when
 * sessionStorage has no cached key — e.g. opened in a new tab). */
export const mintPlaygroundKey = () =>
  api<{ key: string }>("/auth/playground-key", {
    method: "POST",
    credentials: "include",
  });

export const getUsers = () =>
  api<{ users: (User & { disabled: boolean; created_at: number })[] }>(
    "/admin/users",
  );

export const patchUser = (
  id: string,
  body: { role?: string; disabled?: boolean },
) =>
  api<User>(`/admin/users/${encodeURIComponent(id)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
    credentials: "include",
  });

export const getPublicModels = () =>
  api<{ groups: PublicModelGroup[]; aliases: Record<string, string> }>(
    "/public/models",
  );
// -- Cline OAuth (paste-code + automatic redirect flow) ----------------------

export const clineLoginUrl = (callbackUrl: string) =>
  api<ClineLoginUrlResponse>("/admin/cline/oauth/login-url", {
    method: "POST",
    body: JSON.stringify({ callback_url: callbackUrl }),
  });

export const clineAutoConnect = (provider: string, returnPath?: string) =>
  api<ClineAutoConnectResponse>("/admin/cline/oauth/auto-connect", {
    method: "POST",
    body: JSON.stringify({ provider, return_path: returnPath }),
  });

export const clineConnect = (provider: string, code: string) =>
  api<ClineConnectResponse>("/admin/cline/oauth/connect", {
    method: "POST",
    body: JSON.stringify({ provider, code }),
  });

export const clineStatus = (provider: string) =>
  api<ClineStatusResponse>(
    `/admin/cline/oauth/status?provider=${encodeURIComponent(provider)}`,
  );

export const clineRefresh = (provider: string) =>
  api<ClineRefreshResponse>("/admin/cline/oauth/refresh", {
    method: "POST",
    body: JSON.stringify({ provider }),
  });

export const clineDisconnect = (provider: string) =>
  api<ClineDisconnectResponse>("/admin/cline/oauth/disconnect", {
    method: "DELETE",
    body: JSON.stringify({ provider }),
  });

// -- WorkBuddy accounts (auths/ JSON import/export) ---------------------------

export const workbuddyAccounts = () =>
  api<WorkBuddyAccountsResponse>("/admin/workbuddy/accounts");

export const workbuddyImport = (
  provider: string,
  accounts: WorkBuddyAuthFile[] | WorkBuddyAuthFile,
) =>
  api<WorkBuddyImportResponse>("/admin/workbuddy/import", {
    method: "POST",
    body: JSON.stringify({ provider, accounts }),
  });

export const workbuddyExport = (provider?: string) => {
  const q = provider ? `?provider=${encodeURIComponent(provider)}` : "";
  return api<WorkBuddyExportResponse>(`/admin/workbuddy/export${q}`);
};

export const workbuddyRefresh = (provider: string, label: string) =>
  api<WorkBuddyRefreshResponse>("/admin/workbuddy/refresh", {
    method: "POST",
    body: JSON.stringify({ provider, label }),
  });
