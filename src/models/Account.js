import mongoose from "mongoose";

const accountSchema = new mongoose.Schema(
  {
    userId: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
    name: { type: String, required: true, trim: true },
    type: { type: String, enum: ["cash", "bank", "card", "wallet", "other"], default: "cash" },
    currency: { type: String, required: true, uppercase: true },
    balanceMinor: { type: Number, required: true, default: 0 },
    isArchived: { type: Boolean, default: false },
  },
  { timestamps: true }
);

accountSchema.index({ userId: 1, name: 1 }, { unique: true });

export const Account = mongoose.model("Account", accountSchema);
