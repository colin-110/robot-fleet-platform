import { describe, it, expect } from "vitest";
import { matchesQuery } from "./search";

const robot = (overrides = {}) => ({
  robot_id: 1,
  status: "ACTIVE",
  mission_type: "INSPECTION",
  mission_id: "M-04116",
  ...overrides,
});

describe("matchesQuery", () => {
  it("returns every robot when the query is empty or whitespace", () => {
    expect(matchesQuery(robot(), "")).toBe(true);
    expect(matchesQuery(robot(), "   ")).toBe(true);
    expect(matchesQuery(robot(), null)).toBe(true);
    expect(matchesQuery(robot(), undefined)).toBe(true);
  });

  it("matches the id form printed on the card", () => {
    expect(matchesQuery(robot({ robot_id: 7 }), "R7")).toBe(true);
    expect(matchesQuery(robot({ robot_id: 7 }), "r7")).toBe(true);
    expect(matchesQuery(robot({ robot_id: 7 }), "7")).toBe(true);
    expect(matchesQuery(robot({ robot_id: 7 }), " r 7 ")).toBe(true);
  });

  // The bug fixed in 60f5712: searching an id used to match by substring, so
  // "1" dragged R10/R11/R12 along with it.
  it("treats an id-shaped query as exact, not a substring", () => {
    expect(matchesQuery(robot({ robot_id: 1 }), "1")).toBe(true);
    expect(matchesQuery(robot({ robot_id: 10 }), "1")).toBe(false);
    expect(matchesQuery(robot({ robot_id: 11 }), "1")).toBe(false);
    expect(matchesQuery(robot({ robot_id: 1 }), "R1")).toBe(true);
    expect(matchesQuery(robot({ robot_id: 12 }), "R1")).toBe(false);
  });

  it("falls back to substring search across status and mission fields", () => {
    expect(matchesQuery(robot({ status: "OVERHEATING" }), "overheat")).toBe(true);
    expect(matchesQuery(robot({ mission_type: "DELIVERY" }), "deliv")).toBe(true);
    expect(matchesQuery(robot({ mission_id: "M-04116" }), "m-041")).toBe(true);
    expect(matchesQuery(robot(), "nothing-like-this")).toBe(false);
  });

  it("survives robots with null mission fields", () => {
    const idle = robot({ mission_type: null, mission_id: null, status: "STOPPED" });
    expect(matchesQuery(idle, "stopped")).toBe(true);
    expect(matchesQuery(idle, "inspection")).toBe(false);
  });
});
