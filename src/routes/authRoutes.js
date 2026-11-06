import { Router } from "express";
import { z } from "zod";
import { validateBody } from "../middleware/validate.js";
import { requireAuth } from "../middleware/auth.js";
import { methodNotAllowed } from "../middleware/methodNotAllowed.js";
import { register, login, logout, me } from "../controllers/authController.js";

const registerSchema = z.object({
  email: z.string().trim().toLowerCase().email("A valid email is required"),
  password: z.string().min(8, "Password must be at least 8 characters"),
  name: z.string().trim().max(80).optional(),
});

const loginSchema = z.object({
  email: z.string().trim().toLowerCase().email("A valid email is required"),
  password: z.string().min(1, "Password is required"),
});

const router = Router();

router.post("/register", validateBody(registerSchema), register);
router.post("/login", validateBody(loginSchema), login);
router.post("/logout", logout);
router.get("/me", requireAuth, me);

router.all("/register", methodNotAllowed(["POST"]));
router.all("/login", methodNotAllowed(["POST"]));
router.all("/logout", methodNotAllowed(["POST"]));
router.all("/me", methodNotAllowed(["GET"]));

export default router;
