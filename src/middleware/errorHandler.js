export function notFound(req, res) {
  res.status(404).json({ error: `Not found: ${req.method} ${req.originalUrl}` });
}

export function errorHandler(err, _req, res, _next) {
  if (res.headersSent) return;

  let status = err.status || err.statusCode || 500;
  let message = err.message || "Server error";
  let details;

  if (err.name === "ValidationError") {
    status = 400;
    message = "Validation failed";
    details = Object.values(err.errors || {}).map((e) => e.message);
  } else if (err.name === "CastError") {
    status = 400;
    message = `Invalid value for ${err.path}`;
  } else if (err.code === 11000) {
    status = 409;
    message = "That value already exists";
    details = err.keyValue;
  } else if (err instanceof SyntaxError && err.status === 400 && "body" in err) {
    status = 400;
    message = "Invalid JSON body";
  }

  if (status >= 500) console.error(err);

  const payload = { error: message };
  if (details) payload.details = details;
  res.status(status).json(payload);
}
