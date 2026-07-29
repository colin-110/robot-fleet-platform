import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LoginScreen from "./LoginScreen";
import { login } from "../utils/auth";

vi.mock("../utils/auth", () => ({
  login: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

describe("LoginScreen", () => {
  it("submits the credentials the operator typed", async () => {
    const user = userEvent.setup();
    vi.mocked(login).mockResolvedValue({ username: "alice", role: "operator" });
    render(<LoginScreen />);

    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "s3cret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(login).toHaveBeenCalledWith("alice", "s3cret"));
  });

  it("hands the session up once sign-in succeeds", async () => {
    const user = userEvent.setup();
    const session = { username: "alice", role: "operator" };
    vi.mocked(login).mockResolvedValue(session);
    const onSignedIn = vi.fn();
    render(<LoginScreen onSignedIn={onSignedIn} />);

    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "s3cret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(session));
  });

  it("shows the failure as an alert and keeps the operator on the form", async () => {
    const user = userEvent.setup();
    vi.mocked(login).mockRejectedValue(new Error("Invalid username or password"));
    const onSignedIn = vi.fn();
    render(<LoginScreen onSignedIn={onSignedIn} />);

    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Invalid username or password");
    expect(onSignedIn).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  // Guards against double-submit: a second POST while the first is in flight
  // burns a bcrypt verification per click on the server.
  it("disables the button while the request is in flight", async () => {
    const user = userEvent.setup();
    let release;
    vi.mocked(login).mockReturnValue(new Promise((resolve) => { release = resolve; }));
    render(<LoginScreen />);

    await user.type(screen.getByLabelText("Username"), "alice");
    await user.type(screen.getByLabelText("Password"), "s3cret");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    const pending = await screen.findByRole("button", { name: "Signing in…" });
    expect(pending).toBeDisabled();

    release({ username: "alice", role: "operator" });
    await waitFor(() => expect(login).toHaveBeenCalledTimes(1));
  });

  it("masks the password field", () => {
    render(<LoginScreen />);

    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
  });
});
