import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App from "./App";
import { normalizeApiTimestamp } from "./api";

let previousMatchMedia: PropertyDescriptor | undefined;

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  if (previousMatchMedia) Object.defineProperty(window, "matchMedia", previousMatchMedia);
  else Reflect.deleteProperty(window, "matchMedia");
  previousMatchMedia = undefined;
});

describe("MessQry overview", () => {
  it("shows real empty registry states and does not invent telemetry", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
    render(<App />);
    expect(await screen.findByText("Your registry is ready")).toBeInTheDocument();
    expect(screen.getByText(/No live latency, drift, uptime, or cost values are being reported\./)).toBeInTheDocument();
    expect(screen.getByText("No models registered")).toBeInTheDocument();
  });

  it("exposes the six workspace destinations and unfinished sections honestly", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
    const user = userEvent.setup();
    render(<App />);
    await user.click(screen.getByRole("button", { name: "Models" }));
    expect(screen.getByRole("heading", { name: "Models" })).toBeInTheDocument();
    expect(screen.getByText("This section is not part of the current Overview milestone.")).toBeInTheDocument();
  });

  it("shows a recoverable API error state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not load workspace data from the API.");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("uses typed evaluation status even when a model name contains failure wording", async () => {
    const model = { id: "model-1", name: "failed-payment-detector", description: "Forecasting", created_at: "2026-10-19T12:00:00Z" };
    const previousVersion = { id: "version-1", model_id: "model-1", version: 1, artifact_uri: "s3://models/demand/1", framework: "mlflow", metrics: {}, created_at: "2026-10-19T11:00:00Z" };
    const currentVersion = { id: "version-3", model_id: "model-1", version: 3, artifact_uri: "s3://models/demand/3", framework: "mlflow", metrics: {}, created_at: "2026-10-19T12:00:00Z" };
    const deployment = { id: "deployment-1", model_id: "model-1", environment: "staging", model_version: currentVersion, updated_at: "2026-10-19T12:00:00Z" };
    const event = { id: "event-1", action: "promote", reason: null, created_at: "2026-10-19T12:00:00", to_model_version_id: "version-1" };
    const evaluation = { id: "evaluation-1", passed: true, metrics: { accuracy: 0.94 }, created_at: "2026-10-19T12:00:00Z" };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      let payload: unknown = [];
      if (path.endsWith("/api/v1/models")) payload = [model];
      else if (path.endsWith("/api/v1/models/model-1")) payload = { ...model, versions: [previousVersion, currentVersion] };
      else if (path.endsWith("/api/v1/models/model-1/deployments")) payload = [deployment];
      else if (path.endsWith("/staging/history")) payload = [event];
      else if (path.endsWith("/versions/3/evaluations")) payload = [evaluation];
      return { ok: true, json: async () => payload };
    }));

    render(<App />);
    expect(await screen.findByText("failed-payment-detector")).toBeInTheDocument();
    expect(screen.getByText("promote · failed-payment-detector")).toBeInTheDocument();
    const successfulEvaluation = screen.getByText("Evaluation passed · failed-payment-detector");
    expect(successfulEvaluation.closest("li")?.querySelector(".activity-marker")).toHaveClass("success");
    expect(screen.getByText("staging · v1")).toBeInTheDocument();
    expect(screen.queryByText("staging · v3")).not.toBeInTheDocument();
  });

  it("labels the configured deployment environment rather than the Vite build mode", async () => {
    vi.stubEnv("MODE", "production");
    vi.stubEnv("VITE_DEPLOYMENT_ENV", "staging");
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
    render(<App />);
    expect(await screen.findByText("Your registry is ready")).toBeInTheDocument();
    expect(screen.getByText("STAGING")).toBeInTheDocument();
    expect(screen.queryByText("PRODUCTION")).not.toBeInTheDocument();
  });

  it("normalizes naive SQLite timestamps as UTC while preserving explicit offsets", () => {
    expect(normalizeApiTimestamp("2026-10-19T12:00:00")).toBe("2026-10-19T12:00:00Z");
    expect(normalizeApiTimestamp("2026-10-19T12:00:00+05:30")).toBe("2026-10-19T12:00:00+05:30");
    expect(new Date(normalizeApiTimestamp("2026-10-19T12:00:00")).toISOString()).toBe("2026-10-19T12:00:00.000Z");
  });

  it("hides a closed mobile drawer from tab order and manages focus when opened and closed", async () => {
    const media = {
      matches: true,
      media: "(max-width: 760px)",
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
    previousMatchMedia = Object.getOwnPropertyDescriptor(window, "matchMedia");
    Object.defineProperty(window, "matchMedia", { configurable: true, value: vi.fn(() => media) });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => [] }));
    const user = userEvent.setup();
    const { container } = render(<App />);
    const sidebar = container.querySelector("aside.sidebar")!;
    const trigger = screen.getByRole("button", { name: "Open navigation" });
    expect(sidebar).toHaveAttribute("inert");
    expect(sidebar).toHaveAttribute("aria-hidden", "true");

    await user.click(trigger);
    await waitFor(() => expect(screen.getByRole("button", { name: "Overview" })).toHaveFocus());
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(container.querySelector("main")).toHaveAttribute("inert");
    for (let index = 0; index < 5; index += 1) await user.tab();
    expect(screen.getByRole("button", { name: "Settings" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("link", { name: /MessQry AI/ })).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(sidebar).toHaveAttribute("inert");
    expect(container.querySelector("main")).not.toHaveAttribute("inert");

    await user.click(trigger);
    await waitFor(() => expect(screen.getByRole("button", { name: "Overview" })).toHaveFocus());
    await user.click(screen.getByRole("link", { name: /MessQry AI/ }));
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(sidebar).toHaveAttribute("inert");
    expect(container.querySelector("main")).not.toHaveAttribute("inert");
  });
});
