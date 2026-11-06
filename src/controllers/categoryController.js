import { Category } from "../models/Category.js";
import { httpError } from "../lib/httpError.js";

function serialize(category) {
  return {
    id: String(category._id),
    name: category.name,
    type: category.type,
    icon: category.icon || "tag",
  };
}

export async function listCategories(req, res, next) {
  try {
    const filter = { userId: req.userId };
    if (req.query.type) filter.type = req.query.type;
    const items = await Category.find(filter).sort({ type: 1, name: 1 }).lean();
    res.json({ items: items.map(serialize) });
  } catch (e) {
    next(e);
  }
}

export async function createCategory(req, res, next) {
  try {
    const { name, type } = req.body;
    const category = await Category.create({
      userId: req.userId,
      name,
      type,
      icon: "tag",
    });
    res.status(201).json(serialize(category));
  } catch (e) {
    next(e);
  }
}
