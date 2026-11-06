import { Router } from "express";
import { z } from "zod";
import { validateBody } from "../middleware/validate.js";
import { requireAuth } from "../middleware/auth.js";
import { methodNotAllowed } from "../middleware/methodNotAllowed.js";
import { listCategories, createCategory } from "../controllers/categoryController.js";

const createCategorySchema = z.object({
  name: z.string().trim().min(1, "Name is required").max(40, "Name is too long"),
  type: z.enum(["income", "expense"], { message: "Type must be income or expense" }),
});

const router = Router();

router.use(requireAuth);
router.get("/", listCategories);
router.post("/", validateBody(createCategorySchema), createCategory);
router.all("/", methodNotAllowed(["GET", "POST"]));

export default router;
