import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import FleetStatusChart from "./FleetStatusChart";

const robots = (...statuses) => statuses.map((status, i) => ({ robot_id: i + 1, status }));

describe("FleetStatusChart", () => {
  it("names the leading status and its share in the middle", () => {
    render(<FleetStatusChart robots={robots("ACTIVE", "ACTIVE", "ACTIVE", "CHARGING")} />);
    const center = screen.getByTestId("status-center");
    expect(within(center).getByText("75%")).toBeInTheDocument();
    expect(within(center).getByText("Active")).toBeInTheDocument();
  });

  it("lists every status with its count and share", () => {
    render(<FleetStatusChart robots={robots("ACTIVE", "ACTIVE", "CHARGING", "LOW POWER")} />);
    const legend = screen.getByRole("list", { name: "Status counts" });
    const items = within(legend).getAllByRole("listitem").map((li) => li.textContent);
    expect(items).toEqual(["Active2 (50%)", "Charging1 (25%)", "Low Power1 (25%)"]);
  });

  it("reads clearly when the whole fleet is in one status", () => {
    render(<FleetStatusChart robots={robots("ACTIVE", "ACTIVE")} />);
    expect(within(screen.getByTestId("status-center")).getByText("100%")).toBeInTheDocument();
    expect(within(screen.getByRole("list", { name: "Status counts" })).getAllByRole("listitem")).toHaveLength(1);
  });

  it("says so when there are no robots", () => {
    render(<FleetStatusChart robots={[]} />);
    expect(screen.getByText("No data available")).toBeInTheDocument();
  });
});
