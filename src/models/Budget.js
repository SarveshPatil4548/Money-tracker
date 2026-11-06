import mongoose from "mongoose";

const budgetSchema = new mongoose.Schema(
  {
    userId: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
    categoryId: { type: mongoose.Schema.Types.ObjectId, ref: "Category", required: true, index: true },
    year: { type: Number, required: true },
    month: { type: Number, required: true, min: 1, max: 12 },
    limitMinor: { type: Number, required: true },
    currency: { type: String, required: true, uppercase: true },
    notifications: { type: Object, default: {} },
  },
  { timestamps: true }
);

budgetSchema.index({ userId: 1, categoryId: 1, year: 1, month: 1 }, { unique: true });

export const Budget = mongoose.model("Budget", budgetSchema);
