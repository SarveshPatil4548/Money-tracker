import mongoose from "mongoose";

const recurringRuleSchema = new mongoose.Schema(
  {
    userId: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
    accountId: { type: mongoose.Schema.Types.ObjectId, ref: "Account", required: true },
    categoryId: { type: mongoose.Schema.Types.ObjectId, ref: "Category", required: true },
    type: { type: String, enum: ["income", "expense"], required: true },
    amountMinor: { type: Number, required: true },
    currency: { type: String, required: true, uppercase: true },
    description: { type: String, trim: true },
    tags: [{ type: mongoose.Schema.Types.ObjectId, ref: "Tag" }],
    frequency: { type: String, enum: ["daily", "weekly", "monthly", "yearly"], required: true },
    interval: { type: Number, default: 1 },
    dayOfWeek: { type: Number, min: 0, max: 6 },
    dayOfMonth: { type: Number, min: 1, max: 31 },
    startDate: { type: Date, required: true },
    endDate: { type: Date },
    lastRunDate: { type: Date },
    nextRunDate: { type: Date, index: true },
    isPaused: { type: Boolean, default: false },
    metadata: { type: Object, default: {} },
  },
  { timestamps: true }
);

recurringRuleSchema.index({ userId: 1, nextRunDate: 1 });

export const RecurringRule = mongoose.model("RecurringRule", recurringRuleSchema);
