import { beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ModelsPage } from "./ModelsPage";

const { createModelMock, normalizeApiTimestampMock, uploadModelArtifactMock } = vi.hoisted(() => ({
  createModelMock: vi.fn(),
  normalizeApiTimestampMock: vi.fn((value: string) => /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`),
  uploadModelArtifactMock: vi.fn(),
}));

vi.mock("./api", () => ({
  createModel: createModelMock,
  normalizeApiTimestamp: normalizeApiTimestampMock,
  uploadModelArtifact: uploadModelArtifactMock,
}));

const model = {
  id: "model-1",
  name: "risk-classifier",
  description: "Flags risky transactions",
  created_at: "2026-10-19T00:15:00",
  versions: [
    {
      id: "version-1",
      model_id: "model-1",
      version: 1,
      artifact_uri: "s3://private/model",
      package_format: null,
      artifact_sha256: null,
      artifact_size_bytes: null,
      artifact_version_id: null,
      artifact_manifest: null,
      validation_status: "legacy_unverified" as const,
      framework: null,
      metrics: {},
      created_at: "2026-10-19T12:00:00Z",
    },
  ],
};

beforeEach(() => {
  cleanup();
  createModelMock.mockReset();
  uploadModelArtifactMock.mockReset();
  normalizeApiTimestampMock.mockClear();
});

describe("Models registry", () => {
  it("filters models and shows version status and details", async () => {
    const user = userEvent.setup();
    render(<ModelsPage models={[model]} loading={false} loadError={false} onRefresh={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "risk-classifier" })).toBeInTheDocument();
    expect(normalizeApiTimestampMock).toHaveBeenCalledWith("2026-10-19T00:15:00");
    expect(screen.getByText("UNVERIFIED")).toBeInTheDocument();
    await user.type(screen.getByRole("textbox", { name: "Search models" }), "unknown");
    expect(screen.getByText("No models match “unknown”.")).toBeInTheDocument();
  });

  it("registers a model and reports API success", async () => {
    const created = { ...model, versions: [] };
    createModelMock.mockResolvedValue(created);
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    render(<ModelsPage models={[]} loading={false} loadError={false} onRefresh={onRefresh} />);

    await user.click(screen.getByRole("button", { name: "Register first model" }));
    await user.type(screen.getByLabelText("Model name"), "risk-classifier");
    await user.type(screen.getByLabelText(/Description/), "Flags risky transactions");
    await user.click(screen.getByRole("button", { name: "Create model" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Model “risk-classifier” was registered.");
    expect(createModelMock).toHaveBeenCalledWith({ name: "risk-classifier", description: "Flags risky transactions" });
    expect(onRefresh).toHaveBeenCalledWith("model-1");
  });

  it("uploads an artifact for the selected model and reports the validated version", async () => {
    uploadModelArtifactMock.mockResolvedValue({ version: 2 });
    const onRefresh = vi.fn();
    const user = userEvent.setup();
    render(<ModelsPage models={[model]} loading={false} loadError={false} onRefresh={onRefresh} />);
    const file = new File(["zip bytes"], "model.zip", { type: "application/zip" });

    await user.upload(screen.getByLabelText("Model package (.zip)"), file);
    await user.type(screen.getByLabelText(/Framework/), "scikit-learn");
    await user.click(screen.getByRole("button", { name: "Upload version" }));

    expect(uploadModelArtifactMock).toHaveBeenCalled();
    expect(await screen.findByRole("status")).toHaveTextContent("Version v2 was validated and stored.");
    expect(uploadModelArtifactMock).toHaveBeenCalledWith("model-1", file, "scikit-learn");
    expect(onRefresh).toHaveBeenCalledWith("model-1");
  });

  it("shows an upload validation error without claiming success", async () => {
    uploadModelArtifactMock.mockRejectedValue(new Error("Package references a missing path"));
    const user = userEvent.setup();
    render(<ModelsPage models={[model]} loading={false} loadError={false} onRefresh={vi.fn()} />);
    await user.upload(
      screen.getByLabelText("Model package (.zip)"),
      new File(["bad"], "bad.zip", { type: "application/zip" }),
    );
    await user.click(screen.getByRole("button", { name: "Upload version" }));

    expect(uploadModelArtifactMock).toHaveBeenCalled();
    expect(await screen.findByRole("alert")).toHaveTextContent("Package references a missing path");
    expect(screen.queryByText(/was validated and stored/)).not.toBeInTheDocument();
  });
});
