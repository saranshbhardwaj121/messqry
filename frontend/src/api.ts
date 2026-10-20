import type { Deployment, DeploymentEvent, Evaluation, Model, ModelVersion } from "./types";

const apiRoot = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${apiRoot}${path}`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as T;
}

async function responseError(response: Response): Promise<Error> {
  try {
    const payload = (await response.json()) as { error?: { message?: string } };
    if (payload.error?.message) return new Error(payload.error.message);
  } catch {
    // Keep a stable status message when the response body is not JSON.
  }
  return new Error(`Request failed (${response.status})`);
}

async function changeDeployment(
  modelId: string,
  environment: string,
  version: number,
  action: "promote" | "rollback",
  reason: string,
): Promise<Deployment> {
  const response = await fetch(
    `${apiRoot}/api/v1/models/${modelId}/deployments/${environment}${action === "rollback" ? "/rollback" : ""}`,
    {
      method: action === "rollback" ? "POST" : "PUT",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ version, reason: reason || null }),
    },
  );
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as Deployment;
}

export function promoteDeployment(
  modelId: string,
  environment: string,
  version: number,
  reason: string,
): Promise<Deployment> {
  return changeDeployment(modelId, environment, version, "promote", reason);
}

export function rollbackDeployment(
  modelId: string,
  environment: string,
  version: number,
  reason: string,
): Promise<Deployment> {
  return changeDeployment(modelId, environment, version, "rollback", reason);
}

export function loadDeploymentHistory(
  modelId: string,
  environment: string,
): Promise<DeploymentEvent[]> {
  return getJson<DeploymentEvent[]>(
    `/api/v1/models/${modelId}/deployments/${environment}/history`,
  );
}

export function loadDeployments(modelId: string): Promise<Deployment[]> {
  return getJson<Deployment[]>(`/api/v1/models/${modelId}/deployments`);
}

export async function createModel(input: { name: string; description: string }): Promise<Model> {
  const response = await fetch(`${apiRoot}/api/v1/models`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ name: input.name, description: input.description || null }),
  });
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as Model;
}

export async function uploadModelArtifact(
  modelId: string,
  file: File,
  framework: string,
): Promise<ModelVersion> {
  const body = new FormData();
  body.append("file", file);
  if (framework.trim()) body.append("framework", framework.trim());
  const response = await fetch(`${apiRoot}/api/v1/models/${modelId}/versions/artifact`, {
    method: "POST",
    headers: { Accept: "application/json" },
    body,
  });
  if (!response.ok) throw await responseError(response);
  return (await response.json()) as ModelVersion;
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
