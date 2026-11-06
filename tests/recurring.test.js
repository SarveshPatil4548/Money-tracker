import test from "node:test";
import assert from "node:assert";
import { addDays, addMonths, addYears, startOfDay } from "date-fns";

function nextDate(freq, start, opts = {}) {
  const d = new Date(start);
  if (freq === "daily") return addDays(d, opts.interval || 1);
  if (freq === "weekly") return addDays(d, 7 * (opts.interval || 1));
  if (freq === "monthly") return addMonths(d, opts.interval || 1);
  if (freq === "yearly") return addYears(d, opts.interval || 1);
  return d;
}

test("recurring: next dates idempotent shape", () => {
  const s = new Date("2026-01-31T00:00:00Z");
  const m = nextDate("monthly", s);
  assert.ok(m > s);
  const y = nextDate("yearly", s);
  assert.ok(y > m);
});
