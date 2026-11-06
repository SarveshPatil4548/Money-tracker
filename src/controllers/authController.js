import jwt from "jsonwebtoken";
import { User } from "../models/User.js";
import { Account } from "../models/Account.js";
import { Category } from "../models/Category.js";
import { httpError } from "../lib/httpError.js";

const DEFAULT_CATEGORIES = [
  ["Salary", "income"],
  ["Freelance", "income"],
  ["Other Income", "income"],
  ["Rent", "expense"],
  ["Groceries", "expense"],
  ["Transport", "expense"],
  ["Utilities", "expense"],
  ["Entertainment", "expense"],
  ["Dining Out", "expense"],
  ["Shopping", "expense"],
  ["Health", "expense"],
  ["Other Expense", "expense"],
];

function signToken(user) {
  return jwt.sign({ userId: String(user._id) }, process.env.JWT_SECRET || "dev", {
    expiresIn: "7d",
  });
}

function setAuthCookie(res, token) {
  res.cookie("accessToken", token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.COOKIES_SECURE === "true",
    maxAge: 7 * 24 * 60 * 60 * 1000,
    path: "/",
  });
}

function publicUser(user) {
  return {
    id: String(user._id),
    email: user.email,
    name: user.name || "",
    baseCurrency: user.baseCurrency,
  };
}

async function seedDefaults(user) {
  const categoryCount = await Category.countDocuments({ userId: user._id });
  if (categoryCount === 0) {
    await Category.insertMany(
      DEFAULT_CATEGORIES.map(([name, type]) => ({
        userId: user._id,
        name,
        type,
        icon: "tag",
      }))
    );
  }

  const account = await Account.findOne({ userId: user._id, name: "Cash" });
  if (!account) {
    await Account.create({
      userId: user._id,
      name: "Cash",
      type: "cash",
      currency: user.baseCurrency || process.env.BASE_CURRENCY || "INR",
      balanceMinor: 0,
    });
  }
}

export async function register(req, res, next) {
  try {
    const { email, password, name } = req.body;
    const existing = await User.findOne({ email });
    if (existing) throw httpError(409, "An account with that email already exists");

    const user = new User({
      email,
      name: name || "",
      baseCurrency: process.env.BASE_CURRENCY || "INR",
    });
    await user.setPassword(password);
    await user.save();
    await seedDefaults(user);

    setAuthCookie(res, signToken(user));
    res.status(201).json({ user: publicUser(user) });
  } catch (e) {
    next(e);
  }
}

export async function login(req, res, next) {
  try {
    const { email, password } = req.body;
    const user = await User.findOne({ email });
    if (!user || !(await user.comparePassword(password))) {
      throw httpError(401, "Invalid email or password");
    }
    await seedDefaults(user);
    setAuthCookie(res, signToken(user));
    res.json({ user: publicUser(user) });
  } catch (e) {
    next(e);
  }
}

export function logout(_req, res) {
  res.clearCookie("accessToken", { path: "/" });
  res.json({ ok: true });
}

export async function me(req, res, next) {
  try {
    const user = await User.findById(req.userId);
    if (!user) throw httpError(401, "Unauthorized");
    res.json({ user: publicUser(user) });
  } catch (e) {
    next(e);
  }
}
