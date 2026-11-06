import mongoose from "mongoose";

const transactionSchema = new mongoose.Schema(
  {
    userId: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
    accountId: { type: mongoose.Schema.Types.ObjectId, ref: "Account", required: true, index: true },
    categoryId: { type: mongoose.Schema.Types.ObjectId, ref: "Category", required: true, index: true },
    type: { type: String, enum: ["income", "expense", "transfer"], required: true },
    amountMinor: { type: Number, required: true },
    currency: { type: String, required: true, uppercase: true },
    date: { type: Date, required: true, index: true },
    description: { type: String, trim: true },
    tags: [{ type: mongoose.Schema.Types.ObjectId, ref: "Tag" }],
    transferToAccountId: { type: mongoose.Schema.Types.ObjectId, ref: "Account" },
    recurringRuleId: { type: mongoose.Schema.Types.ObjectId, ref: "RecurringRule" },
    metadata: { type: Object, default: {} },
  },
  { timestamps: true }
);

transactionSchema.index({ userId: 1, date: -1 });
transactionSchema.index({ userId: 1, categoryId: 1, date: -1 });
transactionSchema.index({ userId: 1, accountId: 1, date: -1 });

export const Transaction = mongoose.model("Transaction", transactionSchema);
