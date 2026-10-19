import type { Deployment, DeploymentEvent, Evaluation, Model } from "./types";

const apiRoot = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiRoot}${path}`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`Request failed (${response.status})`);
  return (await response.json()) as T;
}

export type OverviewData = {
  models: Model[];
  deployments: Deployment[];
  activity: Array<{
    id: string;
    label: string;
    detail: string;
    time: string;
    status: "success" | "failure" | "neutral";
  }>;
  evaluationCount: number;
};

export function normalizeApiTimestamp(value: string): string {
  return /(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`;
}

export async function loadOverview(): Promise<OverviewData> {
  const models = await getJson<Model[]>("/api/v1/models");
  const details = await Promise.all(
    models.map((model) => getJson<Model>(`/api/v1/models/${model.id}`)),
  );
  const deploymentsByModel = await Promise.all(
    models.map((model) => getJson<Deployment[]>(`/api/v1/models/${model.id}/deployments`)),
  );
  const deployments = deploymentsByModel.flat();
  const histories = await Promise.all(
    deployments.map((deployment) =>
      getJson<DeploymentEvent[]>(
        `/api/v1/models/${deployment.model_id}/deployments/${deployment.environment}/history`,
      ),
    ),
  );
  const versions = details.flatMap((model) => model.versions ?? []);
  const evaluations = await Promise.all(
    versions.map((version) =>
      getJson<Evaluation[]>(
        `/api/v1/models/${version.model_id}/versions/${version.version}/evaluations`,
      ),
    ),
  );

  const modelNames = new Map(details.map((model) => [model.id, model.name]));
  const versionsById = new Map(versions.map((version) => [version.id, version]));
  const deploymentActivity = histories.flatMap((events, index) =>
    events.map((event) => ({
      id: event.id,
      label: `${event.action} · ${modelNames.get(deployments[index].model_id) ?? "Model"}`,
      detail: `${deployments[index].environment} · v${versionsById.get(event.to_model_version_id)?.version ?? "unknown"}`,
      time: normalizeApiTimestamp(event.created_at),
      status: "neutral" as const,
    })),
  );
  const evaluationActivity = evaluations.flatMap((items, index) =>
    items.map((evaluation) => ({
      id: evaluation.id,
      label: `${evaluation.passed ? "Evaluation passed" : "Evaluation failed"} · ${modelNames.get(versions[index].model_id) ?? "Model"}`,
      detail: `Version ${versions[index].version}`,
      time: normalizeApiTimestamp(evaluation.created_at),
      status: evaluation.passed ? "success" as const : "failure" as const,
    })),
  );

  return {
    models: details,
    deployments,
    activity: [...deploymentActivity, ...evaluationActivity]
      .sort((left, right) => Date.parse(right.time) - Date.parse(left.time))
      .slice(0, 6),
    evaluationCount: evaluations.reduce((total, items) => total + items.length, 0),
  };
}
