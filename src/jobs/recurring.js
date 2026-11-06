import cron from "node-cron";
import { RecurringRule } from "../models/RecurringRule.js";
import { Transaction } from "../models/Transaction.js";
import { addDays, addMonths, addYears, startOfDay } from "date-fns";

export function computeNextRun(rule) {
  const start = rule.nextRunDate || rule.startDate;
  const d = new Date(start);
  const iv = rule.interval || 1;
  if (rule.frequency === "daily") return addDays(d, iv);
  if (rule.frequency === "weekly") return addDays(d, 7 * iv);
  if (rule.frequency === "monthly") return addMonths(d, iv);
  if (rule.frequency === "yearly") return addYears(d, iv);
  return addMonths(d, 1);
}

export async function processDueRules() {
  const now = new Date();
  const due = await RecurringRule.find({
    isPaused: false,
    $or: [{ nextRunDate: { $lte: now } }, { lastRunDate: null, startDate: { $lte: now } }],
  }).limit(100);

  for (const rule of due) {
    const runDate = rule.nextRunDate || rule.startDate;
    const dayKey = startOfDay(runDate).toISOString();
    const exists = await Transaction.findOne({
      userId: rule.userId,
      recurringRuleId: rule._id,
      "metadata.runKey": dayKey,
    });
    if (exists) {
      rule.lastRunDate = runDate;
      rule.nextRunDate = computeNextRun(rule);
      await rule.save();
      continue;
    }
    await Transaction.create({
      userId: rule.userId,
      accountId: rule.accountId,
      categoryId: rule.categoryId,
      type: rule.type,
      amountMinor: rule.amountMinor,
      currency: rule.currency,
      date: startOfDay(runDate),
      description: rule.description || "",
      tags: rule.tags || [],
      recurringRuleId: rule._id,
      metadata: { runKey: dayKey },
    });
    rule.lastRunDate = runDate;
    rule.nextRunDate = computeNextRun(rule);
    if (rule.endDate && rule.nextRunDate > rule.endDate) {
      rule.isPaused = true;
    }
    await rule.save();
  }
}

export function startRecurringJobs() {
  cron.schedule("0 */1 * * *", processDueRules);
}
