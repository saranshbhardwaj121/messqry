import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiResponseError } from "./api";
import { EvaluationsPage } from "./EvaluationsPage";

const {
  createEvaluationPolicyMock,
  loadEvaluationPoliciesMock,
  loadVersionEvaluationsMock,
  normalizeApiTimestampMock,
  recordPolicyEvaluationMock,
} = vi.hoisted(() => ({
  createEvaluationPolicyMock: vi.fn(),
  loadEvaluationPoliciesMock: vi.fn(),
  loadVersionEvaluationsMock: vi.fn(),
  normalizeApiTimestampMock: vi.fn((value: string) => /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`),
  recordPolicyEvaluationMock: vi.fn(),
}));

vi.mock("./api", () => ({
  ApiResponseError: class ApiResponseError extends Error {
    constructor(message: string, readonly status: number) { super(message); }
  },
  createEvaluationPolicy: createEvaluationPolicyMock,
  loadEvaluationPolicies: loadEvaluationPoliciesMock,
  loadVersionEvaluations: loadVersionEvaluationsMock,
  normalizeApiTimestamp: normalizeApiTimestampMock,
  recordPolicyEvaluation: recordPolicyEvaluationMock,
}));

const model = {
  id: "model-1",
  name: "risk-classifier",
  description: null,
  created_at: "2026-10-22T00:00:00Z",
  versions: [
    { id: "version-2", model_id: "model-1", version: 2, artifact_uri: "s3://private/v2", framework: "sklearn", metrics: {}, created_at: "2026-10-22T12:00:00Z", validation_status: "validated" as const },
    { id: "version-1", model_id: "model-1", version: 1, artifact_uri: "s3://private/v1", framework: "sklearn", metrics: {}, created_at: "2026-10-21T12:00:00Z", validation_status: "validated" as const },
  ],
};

const productionPolicy = {
  id: "policy-production-v2",
  model_id: "model-1",
  name: "release-quality",
  environment: "production",
  version: 2,
  thresholds: { auc: { operator: "gte" as const, value: 0.9 } },
  created_at: "2026-10-22T10:00:00Z",
};

const passingEvaluation = {
  id: "evaluation-2",
  model_version_id: "version-2",
  passed: true,
  metrics: { auc: 0.94 },
  thresholds: productionPolicy.thresholds,
  created_at: "2026-10-22T12:30:00",
  dataset_name: "fraud-validation",
  dataset_version: "2026-10",
  dataset_sha256: "a".repeat(64),
  evaluator_name: "classification-metrics",
  evaluator_version: "1.4.0",
  policy_id: productionPolicy.id,
  policy_name: productionPolicy.name,
  policy_version: productionPolicy.version,
  policy_environment: productionPolicy.environment,
};

beforeEach(() => {
  cleanup();
  createEvaluationPolicyMock.mockReset();
  loadEvaluationPoliciesMock.mockReset().mockResolvedValue([productionPolicy]);
  loadVersionEvaluationsMock.mockReset().mockImplementation((_modelId: string, version: number) =>
    Promise.resolve(version === 2 ? [passingEvaluation] : []),
  );
  normalizeApiTimestampMock.mockClear();
  recordPolicyEvaluationMock.mockReset().mockResolvedValue(passingEvaluation);
});

describe("EvaluationsPage", () => {
  it("shows current production eligibility, metric outcomes, and provenance", async () => {
    render(<EvaluationsPage models={[model]} loading={false} loadError={false} onRefresh={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Eligible by current policy evidence" })).toBeInTheDocument();
    expect(screen.getByText("release-quality")).toBeInTheDocument();
    expect(screen.getByText(/fraud-validation/)).toBeInTheDocument();
    expect(screen.getByText(/classification-metrics 1\.4\.0/)).toBeInTheDocument();
    expect(screen.getByText(`SHA-256 ${"a".repeat(64)}`)).toBeInTheDocument();
    expect(screen.getAllByText("auc").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/0\.94/).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { name: "Policy result comparison" })).toBeInTheDocument();
  });

  it("records caller-computed metrics and provenance against the selected policy", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn();
    render(<EvaluationsPage models={[model]} loading={false} loadError={false} onRefresh={onRefresh} />);

    await screen.findByRole("heading", { name: "Eligible by current policy evidence" });
    await user.type(screen.getByLabelText("Dataset name"), "fraud-validation");
    await user.type(screen.getByLabelText("Dataset version"), "2026-10");
    await user.type(screen.getByLabelText("Dataset SHA-256"), "b".repeat(64));
    await user.type(screen.getByLabelText("Evaluator"), "classification-metrics");
    await user.type(screen.getByLabelText("Evaluator version"), "1.4.0");
    await user.click(screen.getByRole("button", { name: "Record evaluation" }));

    await waitFor(() => expect(recordPolicyEvaluationMock).toHaveBeenCalledWith("model-1", 2, {
      dataset_name: "fraud-validation",
      dataset_version: "2026-10",
      dataset_sha256: "b".repeat(64),
      evaluator_name: "classification-metrics",
      evaluator_version: "1.4.0",
      policy_id: productionPolicy.id,
      metrics: { auc: 0.95 },
    }));
    expect(onRefresh).toHaveBeenCalled();
  });

  it("shows blocked eligibility when a current production evaluation fails", async () => {
    loadVersionEvaluationsMock.mockResolvedValue([ { ...passingEvaluation, passed: false } ]);
    render(<EvaluationsPage models={[model]} loading={false} loadError={false} onRefresh={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "Production promotion blocked" })).toBeInTheDocument();
    expect(screen.getByText(/latest evaluation for “release-quality” failed/i)).toBeInTheDocument();
  });

  it("blocks eligibility when the latest policy result has a timestamp tie", async () => {
    loadVersionEvaluationsMock.mockResolvedValue([
      passingEvaluation,
      { ...passingEvaluation, id: "evaluation-tied-failure", passed: false },
    ]);
    render(<EvaluationsPage models={[model]} loading={false} loadError={false} onRefresh={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "Production promotion blocked" })).toBeInTheDocument();
    expect(screen.getByText(/ambiguous latest evaluations at the same timestamp/i)).toBeInTheDocument();
  });

  it("creates a new immutable policy version from the policy form", async () => {
    const createdPolicy = {
      ...productionPolicy,
      id: "policy-production-v3",
      name: "latency-quality",
      version: 3,
    };
    const user = userEvent.setup();
    const onRefresh = vi.fn();
    createEvaluationPolicyMock.mockResolvedValue(createdPolicy);
    loadEvaluationPoliciesMock
      .mockResolvedValueOnce([productionPolicy])
      .mockResolvedValue([productionPolicy, createdPolicy]);
    render(<EvaluationsPage models={[model]} loading={false} loadError={false} onRefresh={onRefresh} />);

    await screen.findByRole("heading", { name: "Eligible by current policy evidence" });
    await user.click(screen.getByText("Create policy version"));
    await user.type(screen.getByLabelText("Policy name"), "latency-quality");
    await user.clear(screen.getByLabelText("Version", { selector: "input" }));
    await user.type(screen.getByLabelText("Version", { selector: "input" }), "3");
    await user.click(screen.getByRole("button", { name: "Create immutable policy" }));

    await waitFor(() => expect(createEvaluationPolicyMock).toHaveBeenCalledWith("model-1", {
      name: "latency-quality",
      environment: "production",
      version: 3,
      thresholds: { auc: { operator: "gte", value: 0.9 } },
    }));
    expect(await screen.findByRole("status")).toHaveTextContent("Policy “latency-quality” v3 was created.");
    expect(onRefresh).toHaveBeenCalled();
  });

  it("shows an evaluation-specific access message when evaluation reads are forbidden", async () => {
    loadEvaluationPoliciesMock.mockRejectedValue(new ApiResponseError("Forbidden", 403));
    render(<EvaluationsPage models={[model]} loading={false} loadError={false} onRefresh={vi.fn()} />);

    expect(await screen.findByText("Evaluation access is not granted")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
