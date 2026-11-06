import { Transaction } from "../models/Transaction.js";
import { Category } from "../models/Category.js";
import { Account } from "../models/Account.js";
import { User } from "../models/User.js";
import { toMinor, fromMinor } from "../utils/money.js";
import { httpError } from "../lib/httpError.js";

function toDateString(value) {
  const date = value instanceof Date ? value : new Date(value);
  return date.toISOString().slice(0, 10);
}

function serialize(doc) {
  const hasCategory = doc.categoryId && typeof doc.categoryId === "object" && doc.categoryId.name;
  const category = hasCategory ? doc.categoryId : null;
  return {
    id: String(doc._id),
    type: doc.type,
    amount: Number(fromMinor(doc.amountMinor)),
    amountMinor: doc.amountMinor,
    currency: doc.currency,
    date: toDateString(doc.date),
    description: doc.description || "",
    categoryId: category ? String(category._id) : String(doc.categoryId),
    category: category ? category.name : null,
  };
}

export async function listTransactions(req, res, next) {
  try {
    const { type, categoryId, from, to } = req.query;
    const filter = { userId: req.userId };
    if (type) filter.type = type;
    if (categoryId) filter.categoryId = categoryId;
    if (from || to) {
      filter.date = {};
      if (from) filter.date.$gte = new Date(from);
      if (to) filter.date.$lte = new Date(`${to}T23:59:59.999Z`);
    }

    const items = await Transaction.find(filter)
      .sort({ date: -1, _id: -1 })
      .limit(500)
      .populate("categoryId")
      .lean();

    res.json({ items: items.map(serialize) });
  } catch (e) {
    next(e);
  }
}

export async function createTransaction(req, res, next) {
  try {
    const { type, amount, categoryId, accountId, date, description, tags } = req.body;

    const category = await Category.findOne({ _id: categoryId, userId: req.userId });
    if (!category) throw httpError(400, "Category not found");
    if (category.type !== type) {
      throw httpError(400, `"${category.name}" is an ${category.type} category`);
    }

    const user = await User.findById(req.userId);
    if (!user) throw httpError(401, "Unauthorized");

    let account;
    if (accountId) {
      account = await Account.findOne({ _id: accountId, userId: req.userId });
      if (!account) throw httpError(400, "Account not found");
    } else {
      account = await Account.findOne({ userId: req.userId, name: "Cash" });
      if (!account) {
        account = await Account.create({
          userId: req.userId,
          name: "Cash",
          type: "cash",
          currency: user.baseCurrency || process.env.BASE_CURRENCY || "INR",
          balanceMinor: 0,
        });
      }
    }

    const transaction = await Transaction.create({
      userId: req.userId,
      accountId: account._id,
      categoryId: category._id,
      type,
      amountMinor: toMinor(amount),
      currency: account.currency || user.baseCurrency || "INR",
      date: date ? new Date(date) : new Date(),
      description: description || "",
      tags: tags || [],
    });

    const created = await Transaction.findById(transaction._id).populate("categoryId").lean();
    res.status(201).json(serialize(created));
  } catch (e) {
    next(e);
  }
}
