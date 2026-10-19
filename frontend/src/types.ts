export type ModelVersion = {
  id: string;
  model_id: string;
  version: number;
  artifact_uri: string;
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
  action: string;
  reason: string | null;
  created_at: string;
  to_model_version_id: string;
};

export type Evaluation = {
  id: string;
  passed: boolean;
  metrics: Record<string, number>;
  created_at: string;
};
