import mongoose from "mongoose";

const categorySchema = new mongoose.Schema(
  {
    userId: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
    name: { type: String, required: true, trim: true },
    type: { type: String, enum: ["income", "expense"], required: true },
    icon: { type: String, default: "tag" },
    color: { type: String },
    isArchived: { type: Boolean, default: false },
  },
  { timestamps: true }
);

categorySchema.index({ userId: 1, type: 1, name: 1 }, { unique: true });

export const Category = mongoose.model("Category", categorySchema);
