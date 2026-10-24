/**
 * An error whose message is safe to show to API clients.
 * Anything that is NOT an AppError is treated as a bug: it is logged in
 * full on the server and the client only sees a generic 500 message
 * (same policy as unhandled_exception_handler in the FastAPI app).
 */
export class AppError extends Error {
  /**
   * @param {number} statusCode HTTP status
   * @param {string} message    client-safe message
   * @param {ErrorOptions} [options] pass { cause } to keep the original error
   */
  constructor(statusCode, message, options) {
    super(message, options);
    this.name = 'AppError';
    this.statusCode = statusCode;
  }
}
