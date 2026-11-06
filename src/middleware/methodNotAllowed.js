export function methodNotAllowed(allowed) {
  return function (req, res) {
    res.set("Allow", allowed.join(", "));
    res.status(405).json({
      error: `Method ${req.method} not allowed for ${req.originalUrl}`,
      allow: allowed,
    });
  };
}
