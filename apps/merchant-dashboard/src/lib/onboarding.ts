import type { Payment } from "@/components/payment-table";

export type CurrentUser = {
  id: string;
  full_name: string;
  email: string;
  email_verified: boolean;
  role: string;
};

export type Organization = {
  id: string;
  name: string;
  legal_name?: string;
  business_type?: string;
  county?: string;
  town?: string;
  contact_email: string;
  contact_phone?: string;
  support_email?: string;
  accepted_terms_version?: string;
  accepted_privacy_version?: string;
  current_terms_version: string;
  current_privacy_version: string;
};

export type Merchant = {
  id: string;
  merchant_name: string;
  shortcode: string;
  till_number?: string;
  shortcode_type: "paybill" | "till" | "store_number" | "unknown";
  environment: "sandbox" | "production";
  status: string;
  callback_url: string;
  approval_submitted_at?: string;
  rejection_reason?: string;
};

export type OnboardingStepId = "account" | "business" | "mpesa" | "credentials" | "verification" | "activation";
export type OnboardingStep = { id: OnboardingStepId; number: number; label: string; description: string; complete: boolean; current: boolean };

export type OnboardingSnapshot = {
  user: CurrentUser;
  organization: Organization;
  merchant?: Merchant;
  verificationPayment?: Payment;
};

const credentialTestedStatuses = new Set(["verified", "active", "pending_approval", "rejected", "suspended"]);

export function businessProfileComplete(organization: Organization): boolean {
  return Boolean(organization.legal_name && organization.business_type && organization.county && organization.town && organization.contact_phone && organization.support_email);
}

export function legalConsentCurrent(organization: Organization): boolean {
  return Boolean(
    organization.accepted_terms_version === organization.current_terms_version
    && organization.accepted_privacy_version === organization.current_privacy_version
  );
}

export function credentialsTested(merchant?: Merchant): boolean {
  return Boolean(merchant && credentialTestedStatuses.has(merchant.status));
}

export function deriveOnboarding(snapshot: OnboardingSnapshot): { steps: OnboardingStep[]; next: OnboardingStepId; completed: number; percentage: number } {
  const completion: Record<OnboardingStepId, boolean> = {
    account: snapshot.user.email_verified,
    business: businessProfileComplete(snapshot.organization),
    mpesa: Boolean(snapshot.merchant),
    credentials: credentialsTested(snapshot.merchant),
    verification: snapshot.verificationPayment?.status === "success",
    activation: snapshot.merchant?.status === "active"
  };
  const definitions: Array<[OnboardingStepId, string, string]> = [
    ["account", "Create account", "Owner identity"],
    ["business", "Business profile", "Legal and support details"],
    ["mpesa", "M-PESA setup", "PayBill or Till"],
    ["credentials", "Daraja credentials", "Encrypt and validate"],
    ["verification", "Test payment", "KES 1 callback proof"],
    ["activation", "Activation", "Approval and API access"]
  ];
  const next = definitions.find(([id]) => !completion[id])?.[0] || "activation";
  const completed = Object.values(completion).filter(Boolean).length;
  return {
    next,
    completed,
    percentage: Math.round(completed / definitions.length * 100),
    steps: definitions.map(([id, label, description], index) => ({ id, number: index + 1, label, description, complete: completion[id], current: id === next }))
  };
}

export function normalizeKenyanPhone(input: string): string {
  let phone = input.trim().replace(/[\s()-]/g, "");
  if (phone.startsWith("+")) phone = phone.slice(1);
  if (phone.startsWith("0")) phone = `254${phone.slice(1)}`;
  else if (/^[17]\d{8}$/.test(phone)) phone = `254${phone}`;
  if (!/^254[17]\d{8}$/.test(phone)) throw new Error("Enter a valid Kenyan mobile number");
  return phone;
}

export function verificationReference(merchant: Merchant): string {
  return `LYNXPAY-VERIFY-${merchant.id.slice(0, 8).toUpperCase()}`;
}
