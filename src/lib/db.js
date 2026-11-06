import mongoose from "mongoose";

export async function connectDB(uri) {
  if (!uri) throw new Error("MONGODB_URI is missing");
  if (mongoose.connection.readyState >= 1) return mongoose.connection;
  await mongoose.connect(uri, { maxPoolSize: 10 });
  return mongoose.connection;
}

export function disconnectDB() {
  return mongoose.disconnect();
}
