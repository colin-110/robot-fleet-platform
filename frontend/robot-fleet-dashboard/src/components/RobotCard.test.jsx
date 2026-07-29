import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axios from "axios";
import RobotCard from "./RobotCard";

vi.mock("axios");

const robot = (overrides = {}) => ({
  robot_id: 7,
  battery: 84.2,
  temperature: 41.9,
  speed: 0.79,
  status: "ACTIVE",
  mission_id: "M-04116",
  mission_type: "INSPECTION",
  mission_progress: 63.4,
  runtime_remaining_minutes: 92,
  last_seen: new Date().toISOString(),
  battery_health: 99.8,
  motor_health: 88.1,
  sensor_health: 55.0,
  network_health: 99.9,
  ...overrides,
});

beforeEach(() => {
  vi.clearAllMocks();
  axios.post.mockResolvedValue({ data: { status: "PENDING" } });
});

describe("RobotCard", () => {
  it("labels the unit with the R-prefixed id operators search by", () => {
    render(<RobotCard robot={robot()} />);
    expect(screen.getByText("R7")).toBeInTheDocument();
    expect(screen.getByText("ACTIVE")).toBeInTheDocument();
  });

  it("renders mission type and rounded progress", () => {
    render(<RobotCard robot={robot()} />);
    expect(screen.getByText("INSPECTION · 63%")).toBeInTheDocument();
    expect(screen.getByText("M-04116")).toBeInTheDocument();
  });

  it("shows Idle when the robot has no mission assigned", () => {
    render(<RobotCard robot={robot({ mission_id: null, mission_type: null })} />);
    expect(screen.getByText("Idle")).toBeInTheDocument();
  });

  it("renders n/a rather than a null runtime estimate", () => {
    render(<RobotCard robot={robot({ runtime_remaining_minutes: null })} />);
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });

  // A stopped robot needs Resume; anything else needs the kill switch. Showing
  // Emergency Stop on an already-stopped unit is a dead control.
  it("offers Resume for a stopped robot and Emergency Stop otherwise", () => {
    const { rerender } = render(<RobotCard robot={robot({ status: "ACTIVE" })} />);
    expect(screen.getByRole("button", { name: "Emergency Stop" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume" })).not.toBeInTheDocument();

    rerender(<RobotCard robot={robot({ status: "STOPPED" })} />);
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Emergency Stop" })).not.toBeInTheDocument();
  });

  it("dispatches the command to the versioned endpoint with the API key", async () => {
    const user = userEvent.setup();
    render(<RobotCard robot={robot()} />);

    await user.click(screen.getByRole("button", { name: "Return to Base" }));

    await waitFor(() => expect(axios.post).toHaveBeenCalledTimes(1));
    expect(axios.post).toHaveBeenCalledWith(
      "/api/v1/commands/7",
      { command_type: "RETURN_TO_BASE" },
      { headers: { "X-API-Key": "test-api-key" } },
    );
  });

  it("sends EMERGENCY_STOP and RESUME from their respective buttons", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<RobotCard robot={robot({ status: "ACTIVE" })} />);

    await user.click(screen.getByRole("button", { name: "Emergency Stop" }));
    await waitFor(() =>
      expect(axios.post.mock.calls[0][1]).toEqual({ command_type: "EMERGENCY_STOP" }),
    );

    rerender(<RobotCard robot={robot({ status: "STOPPED" })} />);
    await user.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() =>
      expect(axios.post.mock.calls[1][1]).toEqual({ command_type: "RESUME" }),
    );
  });

  // Guards against double-dispatch: the command API is idempotent, but the
  // button should not let an operator queue five stops on one impatient click.
  it("disables the button while a command is in flight", async () => {
    const user = userEvent.setup();
    let resolvePost;
    axios.post.mockReturnValue(new Promise((resolve) => { resolvePost = resolve; }));
    render(<RobotCard robot={robot()} />);

    await user.click(screen.getByRole("button", { name: "Return to Base" }));

    const pending = await screen.findByRole("button", { name: "Sending…" });
    expect(pending).toBeDisabled();

    resolvePost({ data: {} });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Return to Base" })).toBeEnabled(),
    );
  });

  it("re-enables the button after a failed dispatch", async () => {
    const user = userEvent.setup();
    vi.spyOn(console, "error").mockImplementation(() => {});
    axios.post.mockRejectedValue(new Error("503 Service Unavailable"));
    render(<RobotCard robot={robot()} />);

    await user.click(screen.getByRole("button", { name: "Return to Base" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Return to Base" })).toBeEnabled(),
    );
  });
});
