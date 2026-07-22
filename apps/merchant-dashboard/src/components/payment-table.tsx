import Link from "next/link";
import { Icon } from "./icons";
import { StatusPill } from "./status-pill";
import { EmptyState } from "./ui";

export type Payment = {
  id: string;
  merchant_id?: string;
  external_reference: string;
  invoice_id?: string | null;
  purpose?: string;
  amount: string;
  currency?: string;
  customer_name?: string;
  customer_phone: string;
  description?: string;
  status: string;
  review_status: string;
  review_reason?: string;
  success_source: string;
  receipt_status: string;
  provider_acceptance_state?: string;
  mpesa_receipt_number?: string;
  checkout_request_id?: string;
  created_at: string;
  paid_at?: string;
};

const formatter = new Intl.NumberFormat("en-KE", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
function money(payment: Payment) { return `${payment.currency || "KES"} ${formatter.format(Number(payment.amount))}`; }
function date(value: string) { return new Intl.DateTimeFormat("en-KE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(value)); }
function maskPhone(value: string) { return value.length > 8 ? `${value.slice(0, 6)} ••• ${value.slice(-3)}` : value; }
function human(value?: string) { return value?.replaceAll("_", " ") || "No evidence"; }

export function PaymentTable({ payments }: { payments: Payment[] }) {
  if (!payments.length) return <EmptyState icon="payments" title="No payments match this view" description="New STK Push requests and their callback evidence will appear here. Try clearing your filters if you expected a result." />;

  return <div className="surface overflow-hidden">
    <div className="overflow-x-auto">
      <table className="w-full min-w-[920px] border-collapse text-left text-sm">
        <caption className="sr-only">M-PESA payments</caption>
        <thead><tr className="border-b border-[#e4eae6] bg-[#fafbfa] text-[9px] font-black uppercase tracking-[.14em] text-[#849189]"><th className="px-5 py-3.5">Payment</th><th className="px-4 py-3.5">State</th><th className="px-4 py-3.5 text-right">Amount</th><th className="px-4 py-3.5">Evidence</th><th className="px-4 py-3.5">Receipt</th><th className="px-4 py-3.5">Created</th><th className="w-12 px-4 py-3.5"><span className="sr-only">Open</span></th></tr></thead>
        <tbody>{payments.map(payment => <tr key={payment.id} className="group border-b border-[#edf1ee] bg-white transition last:border-0 hover:bg-[#f9fbf9]">
          <td className="px-5 py-4"><Link className="block font-[750] tracking-[-.01em] text-[#101d16] no-underline group-hover:text-[#087448]" href={`/payments/${payment.id}`}>{payment.external_reference}</Link><span className="mt-1 block text-[11px] text-[#849189]">{payment.customer_name || maskPhone(payment.customer_phone)}</span></td>
          <td className="px-4 py-4"><StatusPill status={payment.status} />{payment.review_status === "needs_review" && <span className="ml-1.5"><StatusPill status="needs_review" /></span>}</td>
          <td className="whitespace-nowrap px-4 py-4 text-right font-[750] tabular-nums">{money(payment)}</td>
          <td className="px-4 py-4"><span className="block text-xs font-semibold capitalize text-[#3f5047]">{human(payment.success_source)} · {human(payment.receipt_status)}</span><span className="mt-1 block text-[10px] capitalize text-[#8a9891]">{human(payment.provider_acceptance_state)}</span></td>
          <td className="px-4 py-4"><code className={`text-[11px] font-bold ${payment.mpesa_receipt_number ? "text-[#26372e]" : "text-[#9aa69f]"}`}>{payment.mpesa_receipt_number || "Awaiting receipt"}</code></td>
          <td className="whitespace-nowrap px-4 py-4 text-[11px] text-[#6d7c74]">{date(payment.created_at)}</td>
          <td className="px-4 py-4"><Link href={`/payments/${payment.id}`} aria-label={`Open payment ${payment.external_reference}`} className="grid size-8 place-items-center rounded-lg text-[#8a9891] no-underline transition group-hover:bg-white group-hover:text-[#087448] group-hover:shadow-sm"><Icon name="arrow" className="size-4" /></Link></td>
        </tr>)}</tbody>
      </table>
    </div>
  </div>;
}
