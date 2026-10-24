/**
 * An error whose message is safe to show to API clients.
 * Anything that is NOT an AppError is treated as a bug: it is logged in full
 * server-side and the client only sees a generic 500 message.
 */
export class AppError extends Error {
  readonly statusCode: number;

  constructor(statusCode: number, message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'AppError';
    this.statusCode = statusCode;
  }
}

/**
 * Thrown when data we are about to send does not match its declared
 * TypeScript contract (see validation/guards.ts). It is deliberately NOT an
 * AppError: it is a server-side bug, so clients get the generic 500.
 */
export class ResponseContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ResponseContractError';
  }
}
