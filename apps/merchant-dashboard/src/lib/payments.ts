export type RetryEvidence = {
  status: string;
  provider_acceptance_state: string;
  receipt_status?: string;
  mpesa_receipt_number?: string;
};

export function canRetryPayment(payment: RetryEvidence): boolean {
  return payment.status === "failed"
    && payment.provider_acceptance_state === "rejected"
    && !payment.mpesa_receipt_number
    && !["present", "enriched_later"].includes(payment.receipt_status || "");
}
