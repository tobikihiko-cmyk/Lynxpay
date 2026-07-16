import { describe, expect, it } from "vitest";
import { deriveOnboarding, legalConsentCurrent, normalizeKenyanPhone, type OnboardingSnapshot } from "./onboarding";

const snapshot: OnboardingSnapshot = {
  user: { id: "user_1", full_name: "Jane Merchant", email: "jane@example.com", email_verified: true, role: "owner" },
  organization: {
    id: "org_1", name: "Acme", legal_name: "Acme Limited", business_type: "Retail", county: "Nairobi", town: "Westlands",
    contact_email: "jane@example.com", contact_phone: "254712345678", support_email: "support@example.com",
    current_terms_version: "2026-07", current_privacy_version: "2026-07"
  }
};

describe("onboarding state", () => {
  it("selects M-PESA setup as the first incomplete stage", () => {
    const result = deriveOnboarding(snapshot);
    expect(result.next).toBe("mpesa");
    expect(result.completed).toBe(2);
  });

  it("requires verified credentials and KES 1 callback proof", () => {
    const merchant = { id: "merchant-abcd", merchant_name: "Acme", shortcode: "174379", shortcode_type: "paybill" as const, environment: "sandbox" as const, status: "verified", callback_url: "https://pay.example/callback" };
    const beforeCallback = deriveOnboarding({ ...snapshot, merchant });
    expect(beforeCallback.next).toBe("verification");
    const afterCallback = deriveOnboarding({ ...snapshot, merchant, verificationPayment: { id: "pay_1", external_reference: "VERIFY", amount: "1", customer_phone: "254712345678", status: "success", review_status: "clear", success_source: "callback", receipt_status: "present", created_at: "2026-07-16T00:00:00Z" } });
    expect(afterCallback.next).toBe("activation");
  });

  it("normalizes supported Kenyan phone formats", () => {
    expect(normalizeKenyanPhone("0712 345 678")).toBe("254712345678");
    expect(normalizeKenyanPhone("+254712345678")).toBe("254712345678");
    expect(() => normalizeKenyanPhone("0201234567")).toThrow("valid Kenyan");
  });

  it("requires both current consent versions", () => {
    expect(legalConsentCurrent(snapshot.organization)).toBe(false);
    expect(legalConsentCurrent({ ...snapshot.organization, accepted_terms_version: "2026-07", accepted_privacy_version: "2026-07" })).toBe(true);
  });
});
