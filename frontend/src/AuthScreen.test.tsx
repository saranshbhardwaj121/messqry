import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { AuthScreen } from "./AuthScreen";

afterEach(cleanup);

describe("Cognito access states", () => {
  it("explains permission denial and keeps machine credentials out of the browser", () => {
    render(<AuthScreen status="denied" message="Missing deployment.write scope." />);

    expect(screen.getByRole("heading", { name: "Workspace access denied" })).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Missing deployment.write scope.");
    expect(screen.getByText(/Do not paste them into this browser/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in with another account" })).toBeInTheDocument();
  });

  it("shows an expiry state with a sign-in action", () => {
    render(<AuthScreen status="expired" />);

    expect(screen.getByRole("heading", { name: "Your session expired" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in again" })).toBeInTheDocument();
  });

  it("does not expose the workspace when production Cognito settings are incomplete", () => {
    render(<AuthScreen status="configuration_required" message="Set Cognito build configuration." />);

    expect(screen.getByRole("heading", { name: "Sign-in setup required" })).toBeInTheDocument();
    expect(screen.getByText("Set Cognito build configuration.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /continue with cognito/i })).not.toBeInTheDocument();
  });
});
