export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(
    `/api${path}`,
    body === undefined
      ? undefined
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
  );
  const data: unknown = await response.json();
  if (!response.ok) {
    const detail =
      typeof data === "object" && data !== null && "detail" in data
        ? data.detail
        : null;
    throw new ApiError(
      response.status,
      typeof detail === "string" ? detail : "Request failed. Please try again.",
    );
  }
  return data as T;
}
export function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Connection failed. Please try again.";
}
export const money = (amount: number): string =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
  }).format(amount);
export const label = (text: string): string =>
  text.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
