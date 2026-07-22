import { createHash } from "node:crypto";

type Entry<T> = { promise: Promise<T>; expiresAt: number };

export class RefreshSingleFlight<T> {
  private readonly entries = new Map<string, Entry<T>>();

  constructor(private readonly successTtlMs = 5_000) {}

  run(refreshToken: string, rotate: () => Promise<T>): Promise<T> {
    const key = createHash("sha256").update(refreshToken).digest("hex");
    const now = Date.now();
    const existing = this.entries.get(key);
    if (existing && existing.expiresAt > now) return existing.promise;
    if (existing) this.entries.delete(key);

    const promise = rotate();
    const entry = { promise, expiresAt: now + this.successTtlMs };
    this.entries.set(key, entry);
    void promise.catch(() => {
      if (this.entries.get(key) === entry) this.entries.delete(key);
    });
    if (this.entries.size > 1_000) {
      for (const [candidate, value] of this.entries) {
        if (value.expiresAt <= now) this.entries.delete(candidate);
      }
    }
    return promise;
  }
}

export const refreshRotations = new RefreshSingleFlight<Record<string, unknown>>();
