import "dotenv/config";
import { createApp } from "./app.js";
import { connectDB } from "./lib/db.js";
import { startRecurringJobs } from "./jobs/recurring.js";

const port = process.env.PORT || 5000;
const app = createApp();

async function start() {
  try {
    await connectDB(process.env.MONGODB_URI);
    console.log("Connected to MongoDB");
    startRecurringJobs();
  } catch (err) {
    console.error("MongoDB connection failed:", err.message);
  }

  app.listen(port, () => {
    console.log(`Server running on :${port}`);
  });
}

start();
