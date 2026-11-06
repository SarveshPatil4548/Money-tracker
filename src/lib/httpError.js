export class HttpError extends Error {
  constructor(status, message, details) {
    super(message);
    this.name = "HttpError";
    this.status = status;
    if (details) this.details = details;
  }
}

export function httpError(status, message, details) {
  return new HttpError(status, message, details);
}
