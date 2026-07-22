import { describe, expect, it, vi } from "vitest";
import { RefreshSingleFlight } from "./refresh-singleflight";

describe("RefreshSingleFlight", () => {
  it("shares one rotation across concurrent requests with the same cookie", async () => {
    const gate = new RefreshSingleFlight<{ access: string }>();
    const rotate = vi.fn(async () => ({ access: "replacement" }));
    const [first, second, third] = await Promise.all([
      gate.run("same-refresh-token", rotate),
      gate.run("same-refresh-token", rotate),
      gate.run("same-refresh-token", rotate),
    ]);
    expect(rotate).toHaveBeenCalledTimes(1);
    expect(first).toEqual(second);
    expect(second).toEqual(third);
  });

  it("does not coalesce different refresh-token families", async () => {
    const gate = new RefreshSingleFlight<string>();
    const rotate = vi.fn(async () => "replacement");
    await Promise.all([gate.run("token-one", rotate), gate.run("token-two", rotate)]);
    expect(rotate).toHaveBeenCalledTimes(2);
  });

  it("evicts a failed rotation so a later request can retry", async () => {
    const gate = new RefreshSingleFlight<string>();
    const rotate = vi
      .fn<() => Promise<string>>()
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce("replacement");
    await expect(gate.run("retry-token", rotate)).rejects.toThrow("temporary failure");
    await expect(gate.run("retry-token", rotate)).resolves.toBe("replacement");
    expect(rotate).toHaveBeenCalledTimes(2);
  });
});
