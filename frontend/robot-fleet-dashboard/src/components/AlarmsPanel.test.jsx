import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axios from "axios";
import AlarmsPanel from "./AlarmsPanel";

vi.mock("axios");
vi.mock("../utils/ticket", () => ({
  getTicket: vi.fn(() => Promise.resolve("v1.console.9999999999.abc.sig")),
}));

const alarm = (overrides = {}) => ({
  id: 4,
  robot_id: 7,
  kind: "BATTERY_LOW",
  severity: "warning",
  state: "ACTIVE",
  message: "Battery at 18%",
  raised_at: "2026-11-01T12:00:00Z",
  ...overrides,
});

beforeEach(() => vi.clearAllMocks());

describe("AlarmsPanel", () => {
  it("says all clear when there is nothing open", () => {
    render(<AlarmsPanel alarms={[]} onChange={() => {}} />);
    expect(screen.getByText("No open alarms.")).toBeInTheDocument();
  });

  it("acknowledges with a console ticket and hands back the updated alarm", async () => {
    axios.post.mockResolvedValue({ data: alarm({ state: "ACKNOWLEDGED", acknowledged_by: "anonymous" }) });
    const onChange = vi.fn();
    render(<AlarmsPanel alarms={[alarm()]} onChange={onChange} />);
    expect(screen.getByText("R7 · Battery at 18%")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Acknowledge" }));
    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const [url, , config] = axios.post.mock.calls[0];
    expect(url).toBe("/api/v1/alarms/4/ack");
    expect(config.headers["X-Console-Ticket"]).toMatch(/^v1\.console/);
    const next = onChange.mock.calls[0][0]([alarm()]);
    expect(next[0].state).toBe("ACKNOWLEDGED");
  });

  it("offers no button for an alarm already acknowledged", () => {
    render(<AlarmsPanel alarms={[alarm({ state: "ACKNOWLEDGED", acknowledged_by: "ana" })]} onChange={() => {}} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText(/acknowledged by ana/)).toBeInTheDocument();
  });
});
