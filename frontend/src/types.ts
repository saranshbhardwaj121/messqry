export type ModelVersion = {
  id: string;
  model_id: string;
  version: number;
  artifact_uri: string;
  package_format?: string | null;
  artifact_sha256?: string | null;
  artifact_size_bytes?: number | null;
  artifact_version_id?: string | null;
  artifact_manifest?: Record<string, unknown> | null;
  validation_status?: "legacy_unverified" | "validated";
  framework: string | null;
  metrics: Record<string, unknown>;
  created_at: string;
};

export type Model = {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  versions?: ModelVersion[];
};

export type Deployment = {
  id: string;
  model_id: string;
  environment: string;
  model_version: ModelVersion;
  updated_at: string;
};

export type DeploymentEvent = {
  id: string;
  deployment_id: string;
  action: string;
  reason: string | null;
  created_at: string;
  from_model_version_id: string | null;
  to_model_version_id: string;
};

export type Evaluation = {
  id: string;
  passed: boolean;
  metrics: Record<string, number>;
  created_at: string;
  dataset_name?: string | null;
  dataset_version?: string | null;
  dataset_sha256?: string | null;
  evaluator_name?: string | null;
  evaluator_version?: string | null;
  policy_id?: string | null;
  policy_name?: string | null;
  policy_version?: number | null;
  policy_environment?: string | null;
};
