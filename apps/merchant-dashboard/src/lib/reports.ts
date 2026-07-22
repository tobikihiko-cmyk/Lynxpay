import type { Payment } from "@/components/payment-table";
import type { Invoice } from "@/lib/invoices";

export type ReportRow = Record<string, string | number | null | undefined>;

export function human(value?: string | null) {
  return value?.replaceAll("_", " ") || "None";
}

export function paymentChannel(payment: Payment) {
  if (payment.external_reference.startsWith("WALKIN-")) return "Walk-in";
  if (payment.invoice_id || payment.external_reference.startsWith("INV-")) return "Invoice";
  if (payment.purpose === "merchant_verification") return "Test";
  return "Payment";
}

export function money(amount: string | number, currency = "KES") {
  return `${currency} ${Number(amount || 0).toLocaleString("en-KE", { maximumFractionDigits: 2 })}`;
}

export function localDateInput(value = new Date()) {
  const offset = value.getTimezoneOffset() * 60_000;
  return new Date(value.getTime() - offset).toISOString().slice(0, 10);
}

export function sameLocalDay(value: string | null | undefined, yyyyMmDd: string) {
  if (!value) return false;
  return localDateInput(new Date(value)) === yyyyMmDd;
}

function csvCell(value: string | number | null | undefined) {
  const text = value == null ? "" : String(value);
  if (!/[",\n\r]/.test(text)) return text;
  return `"${text.replaceAll("\"", "\"\"")}"`;
}

export function rowsToCsv(rows: ReportRow[]) {
  if (!rows.length) return "";
  const headers = Object.keys(rows[0]);
  return [
    headers.map(csvCell).join(","),
    ...rows.map(row => headers.map(header => csvCell(row[header])).join(","))
  ].join("\n");
}

export function downloadCsv(filename: string, rows: ReportRow[]) {
  const csv = rowsToCsv(rows);
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function paymentCsvRow(payment: Payment) {
  return {
    reference: payment.external_reference,
    channel: paymentChannel(payment),
    status: payment.status,
    review_status: payment.review_status,
    amount: payment.amount,
    currency: payment.currency || "KES",
    customer_name: payment.customer_name,
    customer_phone: payment.customer_phone,
    description: payment.description,
    mpesa_receipt_number: payment.mpesa_receipt_number,
    receipt_status: payment.receipt_status,
    success_source: payment.success_source,
    checkout_request_id: payment.checkout_request_id,
    provider_acceptance_state: payment.provider_acceptance_state,
    created_at: payment.created_at,
    paid_at: payment.paid_at
  };
}

export function invoiceCsvRow(invoice: Invoice) {
  return {
    invoice_number: invoice.invoice_number,
    status: invoice.status,
    client_name: invoice.client_name,
    client_phone: invoice.client_phone,
    client_email: invoice.client_email,
    service_title: invoice.service_title,
    amount: invoice.amount,
    currency: invoice.currency,
    payment_link: invoice.payment_link,
    payment_id: invoice.payment_id,
    created_at: invoice.created_at,
    due_at: invoice.due_at,
    paid_at: invoice.paid_at
  };
}
