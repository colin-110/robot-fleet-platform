import { describe, expect, it } from "vitest";
import { mergeAlarm, sortAlarms } from "./alarms";

const alarm = (id, overrides = {}) => ({
  id,
  state: "ACTIVE",
  severity: "warning",
  raised_at: `2026-11-01T12:00:0${id}Z`,
  ...overrides,
});

describe("alarms", () => {
  it("puts active before acknowledged, critical before warning, newest first", () => {
    const sorted = sortAlarms([
      alarm(1, { state: "ACKNOWLEDGED", severity: "critical" }),
      alarm(2),
      alarm(3, { severity: "critical" }),
      alarm(4),
    ]);
    expect(sorted.map((a) => a.id)).toEqual([3, 4, 2, 1]);
  });

  it("replaces an alarm by id and drops it once cleared", () => {
    let list = mergeAlarm([], alarm(1));
    list = mergeAlarm(list, alarm(1, { state: "ACKNOWLEDGED" }));
    expect(list).toEqual([alarm(1, { state: "ACKNOWLEDGED" })]);
    expect(mergeAlarm(list, alarm(1, { state: "CLEARED" }))).toEqual([]);
  });
});
