// Router-state helpers for the deployment views.
//
// "Available" is not the same as "routing". The router picks from available
// deployments, and while at least one non-probation deployment is available it
// skips every healer-restored (probation) one. An unavailable deployment is
// either cooling (a cooldown is still running) or genuinely down. These
// helpers mirror `Router.pick_deployment` so the Models ledger and the Combos
// editor label the same state the same way.

import type { DeploymentInfo } from "@/api/types";

export type DeploymentState = "routing" | "probation" | "cooling" | "down";

export const DEPLOYMENT_STATE_COLOR: Record<DeploymentState, string> = {
  routing: "var(--admin-success)",
  probation: "var(--admin-accent)",
  cooling: "var(--admin-warning)",
  down: "var(--admin-danger)",
};

export const DEPLOYMENT_STATE_WORD: Record<DeploymentState, string> = {
  routing: "routing",
  probation: "probation",
  cooling: "cooling",
  down: "down",
};

export const DEPLOYMENT_STATE_TITLE: Record<DeploymentState, string> = {
  routing: "available and taking traffic",
  probation:
    "restored by the health healer — held back while a healthy sibling is available",
  cooling: "in cooldown — excluded from routing until it expires",
  down: "unavailable — excluded from routing",
};

/**
 * The deployments the router actually picks from: available ones, and among
 * those only the non-probation ones whenever at least one exists. A
 * healer-restored deployment gets zero traffic while a healthy sibling is up,
 * but it is the only candidate when every sibling is held back.
 */
export function routableDeployments(deployments: DeploymentInfo[]): DeploymentInfo[] {
  const available = deployments.filter((d) => d.available);
  const fresh = available.filter((d) => !d.probation);
  return fresh.length > 0 ? fresh : available;
}

/** Classify one deployment against the group's routable set. */
export function deploymentState(
  d: DeploymentInfo,
  routable: readonly DeploymentInfo[],
): DeploymentState {
  if (d.available) return routable.includes(d) ? "routing" : "probation";
  return d.cooldown_remaining_s > 0 ? "cooling" : "down";
}

/** The router clamps weights to >= 1 and has no "pause" semantic. */
export function deploymentWeight(d: DeploymentInfo): number {
  return Math.max(1, d.weight);
}

/**
 * Share of the group's routable weight.
 *
 * The router recomputes weights from the routable candidate set on every
 * pick, and its two-level cross-provider WRR composes back down to exactly
 * this ratio per deployment — so this matches routing, not the config file.
 */
export function deploymentShare(d: DeploymentInfo, deployments: DeploymentInfo[]): number {
  const routable = routableDeployments(deployments);
  if (!routable.includes(d)) return 0;
  const total = routable.reduce((sum, x) => sum + deploymentWeight(x), 0);
  return total > 0 ? deploymentWeight(d) / total : 0;
}

export interface ComboReadiness {
  total: number;
  routable: number;
  probation: number;
  cooling: number;
  down: number;
}

/** Roll a group's deployment states up into counts. */
export function comboReadiness(deployments: DeploymentInfo[]): ComboReadiness {
  const routable = routableDeployments(deployments);
  const routableSet = new Set(routable);
  let probation = 0;
  let cooling = 0;
  let down = 0;
  for (const d of deployments) {
    if (d.available) {
      if (!routableSet.has(d)) probation += 1;
    } else if (d.cooldown_remaining_s > 0) {
      cooling += 1;
    } else {
      down += 1;
    }
  }
  return { total: deployments.length, routable: routable.length, probation, cooling, down };
}

export interface ProviderWeight {
  provider: string;
  weight: number;
}

/**
 * Provider shares of the routable weight, heaviest first. This is what the
 * router's cross-provider WRR actually splits, so a bar drawn from it matches
 * traffic rather than configuration.
 */
export function providerWeightSplit(deployments: DeploymentInfo[]): ProviderWeight[] {
  const routable = routableDeployments(deployments);
  const byProvider = new Map<string, number>();
  for (const d of routable) {
    byProvider.set(d.provider, (byProvider.get(d.provider) ?? 0) + deploymentWeight(d));
  }
  return [...byProvider.entries()]
    .map(([provider, weight]) => ({ provider, weight }))
    .sort((a, b) => b.weight - a.weight || a.provider.localeCompare(b.provider));
}

/**
 * Deployment identities that appear more than once in a group. The admin API
 * addresses a deployment by provider + model_id, so duplicates cannot be
 * weighted or detached independently — the editor must warn about them.
 */
export function duplicateDeploymentIdents(deployments: DeploymentInfo[]): string[] {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const d of deployments) {
    const ident = `${d.provider}/${d.model_id}`;
    if (seen.has(ident)) duplicates.add(ident);
    else seen.add(ident);
  }
  return [...duplicates];
}
