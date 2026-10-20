import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DeploymentsPage } from "./DeploymentsPage";

const {
  loadDeploymentHistoryMock,
  loadDeploymentsMock,
  normalizeApiTimestampMock,
  promoteDeploymentMock,
  rollbackDeploymentMock,
} = vi.hoisted(() => ({
  loadDeploymentHistoryMock: vi.fn(),
  loadDeploymentsMock: vi.fn(),
  normalizeApiTimestampMock: vi.fn((value: string) => /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`),
  promoteDeploymentMock: vi.fn(),
  rollbackDeploymentMock: vi.fn(),
}));

vi.mock("./api", () => ({
  loadDeploymentHistory: loadDeploymentHistoryMock,
  loadDeployments: loadDeploymentsMock,
  normalizeApiTimestamp: normalizeApiTimestampMock,
  promoteDeployment: promoteDeploymentMock,
  rollbackDeployment: rollbackDeploymentMock,
}));

const model = {
  id: "model-1",
  name: "risk-classifier",
  description: null,
  created_at: "2026-10-19T00:00:00Z",
  versions: [
    {
      id: "version-2",
      model_id: "model-1",
      version: 2,
      artifact_uri: "s3://private/model-v2",
      package_format: "mlflow_pyfunc",
      artifact_sha256: "abc",
      artifact_size_bytes: 100,
      artifact_version_id: null,
      artifact_manifest: null,
      validation_status: "validated" as const,
      framework: "sklearn",
      metrics: {},
      created_at: "2026-10-19T12:00:00Z",
    },
    {
      id: "version-1",
      model_id: "model-1",
      version: 1,
      artifact_uri: "s3://private/model-v1",
      package_format: "mlflow_pyfunc",
      artifact_sha256: "def",
      artifact_size_bytes: 90,
      artifact_version_id: null,
      artifact_manifest: null,
      validation_status: "validated" as const,
      framework: "sklearn",
      metrics: {},
      created_at: "2026-10-18T12:00:00Z",
    },
  ],
};

const currentDeployment = {
  id: "deployment-1",
  model_id: "model-1",
  environment: "staging",
  model_version: model.versions[0],
  updated_at: "2026-10-19T12:00:00",
};

beforeEach(() => {
  cleanup();
  loadDeploymentHistoryMock.mockReset();
  loadDeploymentsMock.mockReset();
  normalizeApiTimestampMock.mockClear();
  promoteDeploymentMock.mockReset();
  rollbackDeploymentMock.mockReset();
  loadDeploymentsMock.mockResolvedValue([currentDeployment]);
  loadDeploymentHistoryMock.mockResolvedValue([
    {
      id: "event-1",
      deployment_id: "deployment-1",
      action: "deploy",
      reason: "first release",
      created_at: "2026-10-19T12:00:00",
      from_model_version_id: null,
      to_model_version_id: "version-2",
    },
  ]);
});

describe("Deployments page", () => {
  it("shows current selection and historical version from the API", async () => {
    render(<DeploymentsPage models={[model]} loading={false} loadError={false} onRefresh={vi.fn()} />);

    expect(await screen.findByText(/CURRENT VERSION \/ STAGING/)).toBeInTheDocument();
    expect(screen.getByText("v2", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByText("Initial deployment")).toBeInTheDocument();
    expect(screen.getByText("v2 · first release")).toBeInTheDocument();
    expect(normalizeApiTimestampMock).toHaveBeenCalledWith("2026-10-19T12:00:00");
  });

  it("keeps the model selector available when the initially selected model has no versions", async () => {
    const user = userEvent.setup();
    const emptyModel = { ...model, id: "model-empty", name: "new-model", versions: [] };
    render(
      <DeploymentsPage
        models={[emptyModel, model]}
        loading={false}
        loadError={false}
        onRefresh={vi.fn()}
      />,
    );

    expect(await screen.findByText(/No deployable version is available/)).toBeInTheDocument();
    const modelSelect = screen.getByLabelText("Model");
    await user.selectOptions(modelSelect, "model-1");

    expect(await screen.findByLabelText("Registered version")).toBeInTheDocument();
    expect(await screen.findByText("Initial deployment")).toBeInTheDocument();
  });

  it("requires explicit confirmation before promoting a selected version", async () => {
    const user = userEvent.setup();
    const onRefresh = vi.fn();
    promoteDeploymentMock.mockResolvedValue(currentDeployment);
    render(<DeploymentsPage models={[model]} loading={false} loadError={false} onRefresh={onRefresh} />);

    await screen.findByText("Initial deployment");
    await user.selectOptions(screen.getByLabelText("Registered version"), "version-1");
    await user.type(screen.getByLabelText(/Change reason/), "approved release");
    await user.click(screen.getByRole("button", { name: "Promote version" }));
    expect(screen.getByRole("group", { name: "Confirm deployment change" })).toHaveTextContent("risk-classifier v1");
    expect(promoteDeploymentMock).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm promote" }));
    expect(promoteDeploymentMock).toHaveBeenCalledWith("model-1", "staging", 1, "approved release");
    expect(await screen.findByRole("status")).toHaveTextContent("Selected risk-classifier v1 for staging.");
    expect(onRefresh).toHaveBeenCalledOnce();
  });

  it("shows a recoverable error when deployment history cannot load", async () => {
    loadDeploymentHistoryMock.mockRejectedValue(new Error("access denied"));
    render(<DeploymentsPage models={[model]} loading={false} loadError={false} onRefresh={vi.fn()} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("access denied");
  });
});
