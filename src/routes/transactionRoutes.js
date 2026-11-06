import { Router } from "express";
import { z } from "zod";
import { validateBody } from "../middleware/validate.js";
import { requireAuth } from "../middleware/auth.js";
import { methodNotAllowed } from "../middleware/methodNotAllowed.js";
import { listTransactions, createTransaction } from "../controllers/transactionController.js";

const objectId = z.string().regex(/^[0-9a-fA-F]{24}$/, "A valid id is required");

const createTransactionSchema = z.object({
  type: z.enum(["expense", "income"], { message: "Type must be expense or income" }),
  amount: z.coerce.number().positive("Amount must be greater than 0"),
  categoryId: objectId,
  accountId: objectId.optional(),
  date: z.string().min(1).optional(),
  description: z.string().trim().max(200).optional(),
  tags: z.array(z.string()).optional(),
});

const router = Router();

router.use(requireAuth);
router.get("/", listTransactions);
router.post("/", validateBody(createTransactionSchema), createTransaction);
router.all("/", methodNotAllowed(["GET", "POST"]));

export default router;
