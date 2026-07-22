import { describe, expect, it } from "vitest";
import { mutationIsSameOrigin } from "./request-security";

describe("mutationIsSameOrigin", () => {
  it("accepts same-origin browser mutations", () => expect(mutationIsSameOrigin({ origin: "https://pay.lynxpay.co.ke", host: "pay.lynxpay.co.ke", fetchSite: "same-origin" })).toBe(true));
  it("rejects cross-site Fetch Metadata even with a matching origin", () => expect(mutationIsSameOrigin({ origin: "https://pay.lynxpay.co.ke", host: "pay.lynxpay.co.ke", fetchSite: "cross-site" })).toBe(false));
  it("fails closed when origin evidence is missing", () => expect(mutationIsSameOrigin({ origin: null, host: "pay.lynxpay.co.ke", fetchSite: null })).toBe(false));
});
