"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";
import type { Payment } from "@/components/payment-table";
import { StatusPill } from "@/components/status-pill";
import { EmptyState, LoadingTable, MetricCard, PageHeader } from "@/components/ui";
import { Icon } from "@/components/icons";
import { api } from "@/lib/api";
import type { Invoice } from "@/lib/invoices";
import {
  downloadCsv,
  human,
  invoiceCsvRow,
  localDateInput,
  money,
  paymentChannel,
  paymentCsvRow,
  sameLocalDay
} from "@/lib/reports";

type Merchant = {
  id: string;
  merchant_name: string;
  shortcode: string;
  shortcode_type: string;
  status: string;
};
type MerchantPage = { items: Merchant[] };
type PaymentPage = { items: Payment[] };
type InvoicePage = { items: Invoice[] };
type ReportId = "daily" | "exceptions" | "invoices" | "walkins" | "evidence";

const reports: { id: ReportId; label: string }[] = [
  { id: "daily", label: "Daily Collections" },
  { id: "exceptions", label: "Pending / Failed" },
  { id: "invoices", label: "Invoice Reconciliation" },
  { id: "walkins", label: "Walk-in Sales" },
  { id: "evidence", label: "M-PESA Evidence" }
];

function dateTime(value?: string | null) {
  if (!value) return "Not recorded";
  return new Intl.DateTimeFormat("en-KE", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

function maskPhone(value?: string | null) {
  if (!value) return "No phone";
  return value.length > 8 ? `${value.slice(0, 6)} *** ${value.slice(-3)}` : value;
}

function sumPayments(payments: Payment[]) {
  return payments.reduce((total, payment) => total + Number(payment.amount || 0), 0);
}

function shortMerchant(merchant?: Merchant) {
  if (!merchant) return "All merchants";
  return `${merchant.merchant_name} · ${merchant.shortcode_type} ${merchant.shortcode}`;
}

export default function ReportsPage() {
  const [report, setReport] = useState<ReportId>("daily");
  const [date, setDate] = useState(localDateInput());
  const [merchantId, setMerchantId] = useState("");
  const [merchants, setMerchants] = useState<Merchant[]>([]);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.all([
      api<MerchantPage>("/merchants?limit=100"),
      api<PaymentPage>("/payments?limit=500"),
      api<InvoicePage>("/invoices?limit=500")
    ]).then(([merchantResult, paymentResult, invoiceResult]) => {
      if (!active) return;
      setMerchants(merchantResult.items);
      setPayments(paymentResult.items);
      setInvoices(invoiceResult.items);
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "Could not load reports");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const scopedPayments = useMemo(() => payments.filter(payment => !merchantId || payment.merchant_id === merchantId), [payments, merchantId]);
  const scopedInvoices = useMemo(() => invoices.filter(invoice => !merchantId || invoice.merchant_id === merchantId), [invoices, merchantId]);
  const dailyCollections = useMemo(() => scopedPayments.filter(payment => payment.status === "success" && sameLocalDay(payment.paid_at || payment.created_at, date)), [scopedPayments, date]);
  const pendingFailed = useMemo(() => scopedPayments.filter(payment => ["created", "pending", "failed", "unknown"].includes(payment.status) || payment.review_status === "needs_review"), [scopedPayments]);
  const invoiceRows = useMemo(() => scopedInvoices, [scopedInvoices]);
  const walkIns = useMemo(() => scopedPayments.filter(payment => payment.external_reference.startsWith("WALKIN-") && sameLocalDay(payment.paid_at || payment.created_at, date)), [scopedPayments, date]);
  const evidenceRows = useMemo(() => scopedPayments.filter(payment => payment.checkout_request_id || payment.mpesa_receipt_number || payment.receipt_status !== "missing" || payment.success_source), [scopedPayments]);
  const activeMerchant = merchants.find(merchant => merchant.id === merchantId);
  const successWithReceipt = scopedPayments.filter(payment => payment.status === "success" && payment.mpesa_receipt_number).length;
  const successCount = scopedPayments.filter(payment => payment.status === "success").length;

  const currentRows = report === "invoices" ? invoiceRows : report === "daily" ? dailyCollections : report === "exceptions" ? pendingFailed : report === "walkins" ? walkIns : evidenceRows;
  const exportDisabled = currentRows.length === 0;

  function exportReport() {
    if (report === "invoices") {
      downloadCsv(`lynxpay-invoice-reconciliation-${date}.csv`, invoiceRows.map(invoiceCsvRow));
      return;
    }
    const rows = (currentRows as Payment[]).map(paymentCsvRow);
    downloadCsv(`lynxpay-${report}-${date}.csv`, rows);
  }

  return <section>
    <PageHeader
      eyebrow="Reconciliation reports"
      title="Reports"
      description="Payment truth reports for collections, invoices, walk-ins, pending payments and M-PESA receipt evidence."
      action={<button className="primary" type="button" onClick={exportReport} disabled={exportDisabled}><Icon name="audit" className="size-4" />Export CSV</button>}
    />

    <div className="my-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Daily collections" value={money(sumPayments(dailyCollections))} detail={`${dailyCollections.length} successful payments on ${date}`} icon="payments" tone="positive" />
      <MetricCard label="Pending / failed" value={String(pendingFailed.length)} detail="Payments requiring follow-up" icon="review" tone={pendingFailed.length ? "warning" : "neutral"} />
      <MetricCard label="Open invoices" value={String(scopedInvoices.filter(invoice => !["paid", "voided"].includes(invoice.status)).length)} detail={`${scopedInvoices.length} invoices in scope`} icon="invoices" />
      <MetricCard label="Receipt coverage" value={successCount ? `${Math.round((successWithReceipt / successCount) * 100)}%` : "0%"} detail={`${successWithReceipt}/${successCount} successful payments have receipts`} icon="check" tone={successCount && successWithReceipt === successCount ? "positive" : "neutral"} />
    </div>

    <section className="surface p-4 md:p-5">
      <div className="flex flex-wrap items-end gap-3">
        <label className="field min-w-[220px] flex-1">Merchant
          <select value={merchantId} onChange={event => setMerchantId(event.target.value)}>
            <option value="">All merchants</option>
            {merchants.map(merchant => <option key={merchant.id} value={merchant.id}>{merchant.merchant_name} · {merchant.shortcode_type} {merchant.shortcode}</option>)}
          </select>
        </label>
        <label className="field w-[180px]">Business date
          <input type="date" value={date} onChange={event => setDate(event.target.value)} />
        </label>
        <button className="secondary min-h-11" type="button" onClick={() => { setDate(localDateInput()); setMerchantId(""); }}><Icon name="refresh" className="size-4" />Reset</button>
      </div>
      <div className="mt-4 flex gap-2 overflow-x-auto pb-1">
        {reports.map(item => <button key={item.id} type="button" onClick={() => setReport(item.id)} className={`shrink-0 rounded-xl px-4 py-2.5 text-xs font-black transition ${report === item.id ? "bg-[#07150e] text-white" : "border border-[#dfe6e1] bg-white text-[#52635a] hover:border-[#b8c6bd]"}`}>{item.label}</button>)}
      </div>
      {error && <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-800">{error}</p>}
      <p className="mt-4 text-xs font-semibold text-[#75857c]">{shortMerchant(activeMerchant)} · {currentRows.length} rows</p>
    </section>

    <div className="mt-6">
      {loading ? <LoadingTable /> : report === "daily" ? <DailyCollectionsTable payments={dailyCollections} /> : report === "exceptions" ? <PendingFailedTable payments={pendingFailed} /> : report === "invoices" ? <InvoiceReportTable invoices={invoiceRows} /> : report === "walkins" ? <WalkInReportTable payments={walkIns} /> : <EvidenceTable payments={evidenceRows} />}
    </div>
  </section>;
}

function DailyCollectionsTable({ payments }: { payments: Payment[] }) {
  if (!payments.length) return <EmptyState icon="payments" title="No collections for this day" description="Successful M-PESA payments for the selected business date will appear here." />;
  return <ReportTable minWidth="1040px" headings={["Paid", "Reference", "Channel", "Customer", "Amount", "Receipt", "Evidence", "Open"]}>
    {payments.map(payment => <tr key={payment.id}>
      <Cell>{dateTime(payment.paid_at || payment.created_at)}</Cell>
      <Cell strong>{payment.external_reference}</Cell>
      <Cell>{paymentChannel(payment)}</Cell>
      <Cell>{payment.customer_name || maskPhone(payment.customer_phone)}</Cell>
      <Cell align="right" strong>{money(payment.amount, payment.currency)}</Cell>
      <Cell mono>{payment.mpesa_receipt_number || "Awaiting receipt"}</Cell>
      <Cell>{human(payment.success_source)} · {human(payment.receipt_status)}</Cell>
      <Cell><OpenPayment id={payment.id} /></Cell>
    </tr>)}
  </ReportTable>;
}

function PendingFailedTable({ payments }: { payments: Payment[] }) {
  if (!payments.length) return <EmptyState icon="check" title="No pending or failed payments" description="The reconciliation queue is clear for the current merchant scope." />;
  return <ReportTable minWidth="1100px" headings={["Created", "Reference", "State", "Amount", "Customer", "Checkout", "Reason", "Open"]}>
    {payments.map(payment => <tr key={payment.id}>
      <Cell>{dateTime(payment.created_at)}</Cell>
      <Cell strong>{payment.external_reference}</Cell>
      <Cell><StatusPill status={payment.status} />{payment.review_status === "needs_review" && <span className="ml-1.5"><StatusPill status="needs_review" /></span>}</Cell>
      <Cell align="right" strong>{money(payment.amount, payment.currency)}</Cell>
      <Cell>{maskPhone(payment.customer_phone)}</Cell>
      <Cell mono>{payment.checkout_request_id || "Not assigned"}</Cell>
      <Cell>{payment.review_reason || payment.provider_acceptance_state || payment.receipt_status}</Cell>
      <Cell><OpenPayment id={payment.id} /></Cell>
    </tr>)}
  </ReportTable>;
}

function InvoiceReportTable({ invoices }: { invoices: Invoice[] }) {
  if (!invoices.length) return <EmptyState icon="invoices" title="No invoices in scope" description="Created invoices and their payment match status will appear here." />;
  return <ReportTable minWidth="1080px" headings={["Created", "Invoice", "Client", "Service", "Amount", "Status", "Paid", "Link"]}>
    {invoices.map(invoice => <tr key={invoice.id}>
      <Cell>{dateTime(invoice.created_at)}</Cell>
      <Cell strong>{invoice.invoice_number}</Cell>
      <Cell>{invoice.client_name}</Cell>
      <Cell>{invoice.service_title}</Cell>
      <Cell align="right" strong>{money(invoice.amount, invoice.currency)}</Cell>
      <Cell><StatusPill status={invoice.status} /></Cell>
      <Cell>{dateTime(invoice.paid_at)}</Cell>
      <Cell>{invoice.payment_id ? <Link className="font-bold text-emerald-700 no-underline" href={`/payments/${invoice.payment_id}`}>Payment</Link> : <Link className="font-bold text-[#52635a] no-underline" href={`/pay/${invoice.public_id}`}>Link</Link>}</Cell>
    </tr>)}
  </ReportTable>;
}

function WalkInReportTable({ payments }: { payments: Payment[] }) {
  if (!payments.length) return <EmptyState icon="payments" title="No walk-in sales for this day" description="Counter STK prompts with WALKIN references will appear here." action={<Link className="secondary no-underline" href="/walk-ins"><Icon name="payments" className="size-4" />Open Walk-ins</Link>} />;
  return <ReportTable minWidth="1040px" headings={["Time", "Reference", "Description", "Customer", "Amount", "Status", "Receipt", "Open"]}>
    {payments.map(payment => <tr key={payment.id}>
      <Cell>{dateTime(payment.paid_at || payment.created_at)}</Cell>
      <Cell strong>{payment.external_reference}</Cell>
      <Cell>{payment.description || "Walk-in sale"}</Cell>
      <Cell>{maskPhone(payment.customer_phone)}</Cell>
      <Cell align="right" strong>{money(payment.amount, payment.currency)}</Cell>
      <Cell><StatusPill status={payment.status} /></Cell>
      <Cell mono>{payment.mpesa_receipt_number || "Awaiting receipt"}</Cell>
      <Cell><OpenPayment id={payment.id} /></Cell>
    </tr>)}
  </ReportTable>;
}

function EvidenceTable({ payments }: { payments: Payment[] }) {
  if (!payments.length) return <EmptyState icon="shield" title="No M-PESA evidence yet" description="STK checkout IDs, callbacks, receipts and status-query evidence will appear here." />;
  return <ReportTable minWidth="1180px" headings={["Reference", "State", "Amount", "Receipt", "Checkout request", "Source", "Receipt status", "Review", "Open"]}>
    {payments.map(payment => <tr key={payment.id}>
      <Cell strong>{payment.external_reference}</Cell>
      <Cell><StatusPill status={payment.status} /></Cell>
      <Cell align="right" strong>{money(payment.amount, payment.currency)}</Cell>
      <Cell mono>{payment.mpesa_receipt_number || "Missing"}</Cell>
      <Cell mono>{payment.checkout_request_id || "Not assigned"}</Cell>
      <Cell>{human(payment.success_source || payment.provider_acceptance_state)}</Cell>
      <Cell>{human(payment.receipt_status)}</Cell>
      <Cell>{payment.review_status === "needs_review" ? <StatusPill status="needs_review" /> : human(payment.review_status)}</Cell>
      <Cell><OpenPayment id={payment.id} /></Cell>
    </tr>)}
  </ReportTable>;
}

function ReportTable({ headings, children, minWidth }: { headings: string[]; children: ReactNode; minWidth: string }) {
  return <div className="surface overflow-hidden">
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm" style={{ minWidth }}>
        <thead><tr className="border-b border-[#e4eae6] bg-[#fafbfa] text-[9px] font-black uppercase tracking-[.14em] text-[#849189]">{headings.map(heading => <th key={heading} className="px-4 py-3.5">{heading}</th>)}</tr></thead>
        <tbody className="divide-y divide-[#edf1ee] bg-white">{children}</tbody>
      </table>
    </div>
  </div>;
}

function Cell({ children, strong = false, mono = false, align = "left" }: { children: ReactNode; strong?: boolean; mono?: boolean; align?: "left" | "right" }) {
  return <td className={`px-4 py-4 ${align === "right" ? "text-right tabular-nums" : ""} ${strong ? "font-[750] text-[#101d16]" : "text-[#52635a]"} ${mono ? "break-all font-mono text-[11px]" : ""}`}>{children}</td>;
}

function OpenPayment({ id }: { id: string }) {
  return <Link href={`/payments/${id}`} aria-label="Open payment evidence" className="grid size-8 place-items-center rounded-lg text-[#8a9891] no-underline transition hover:bg-[#f5f7f5] hover:text-[#087448]"><Icon name="arrow" className="size-4" /></Link>;
}
