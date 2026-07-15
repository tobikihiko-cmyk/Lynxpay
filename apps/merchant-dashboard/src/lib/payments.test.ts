import { describe, expect, it } from "vitest";
import { canRetryPayment } from "./payments";

describe("canRetryPayment", () => {
  it("allows only definitely rejected failures without receipt evidence", () => {
    expect(canRetryPayment({ status: "failed", provider_acceptance_state: "rejected", receipt_status: "not_applicable" })).toBe(true);
    expect(canRetryPayment({ status: "success", provider_acceptance_state: "accepted", receipt_status: "present", mpesa_receipt_number: "ABC" })).toBe(false);
    expect(canRetryPayment({ status: "unknown", provider_acceptance_state: "uncertain" })).toBe(false);
  });
});
