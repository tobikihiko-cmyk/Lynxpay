import { apiErrorMessage } from "./errors";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/lynxpay${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers }
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new ApiError(response.status, apiErrorMessage(payload, "LynxPay request failed"));
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}
