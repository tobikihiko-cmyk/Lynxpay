"use client";

import Link from "next/link";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { Payment } from "@/components/payment-table";
import { Icon } from "@/components/icons";
import { StatusPill } from "@/components/status-pill";
import { MetricCard, PageHeader } from "@/components/ui";
import { canRetryPayment } from "@/lib/payments";

type Attempt = { id: string; attempt_number: number; attempt_type: string; status: string; checkout_request_id?: string; response_description?: string; created_at: string };
type Callback = { id: string; result_code?: string; result_description?: string; processing_status: string; mpesa_receipt_number?: string; duplicate_of_callback_id?: string; received_at: string };
type Ledger = { id: string; event_type: string; status_from?: string; status_to?: string; details?: Record<string, unknown>; created_at: string };
type StatusCheck = { id: string; outcome: string; result_code?: string; result_description?: string; checked_at: string };
type Timeline = { payment: Payment; attempts: Attempt[]; callbacks: Callback[]; ledger: Ledger[]; status_checks: StatusCheck[] };
type EvidenceEvent = { id: string; category: string; title: string; description: string; timestamp: string; status?: string };

function formatDate(value: string) { return new Intl.DateTimeFormat("en-KE", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)); }
function human(value?: string) { return value?.replaceAll("_", " ") || "Not available"; }

function RetryDialog({ payment, onClose, onComplete }: { payment: Payment; onClose: () => void; onComplete: () => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true); setError("");
    try { await api(`/payments/${payment.id}/retry`, { method: "POST", body: JSON.stringify({ reason }) }); await onComplete(); onClose(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Retry failed"); }
    finally { setSubmitting(false); }
  }
  return <div className="fixed inset-0 z-50 grid place-items-center bg-[#041009]/65 p-4 backdrop-blur-sm" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
    <form role="dialog" aria-modal="true" aria-labelledby="retry-title" className="surface w-full max-w-lg p-6 shadow-[var(--shadow-lg)]" onSubmit={submit}>
      <div className="flex items-start justify-between gap-4"><div><p className="eyeline">Controlled operation</p><h2 id="retry-title" className="mt-2 text-2xl font-bold tracking-[-.04em]">Retry this STK Push?</h2></div><button type="button" className="quiet-button !size-9 !p-0" onClick={onClose} aria-label="Close"><Icon name="close" className="size-4"/></button></div>
      <div className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50/60 p-4 text-xs leading-5 text-emerald-900"><strong className="block">LynxPay found definite rejection evidence.</strong>This creates a new attempt on payment <code>{payment.external_reference}</code>. It does not create another payment record.</div>
      <label className="field mt-5">Operational reason<textarea required minLength={8} autoFocus value={reason} onChange={event => setReason(event.target.value)} placeholder="Example: Customer requested a retry after correcting insufficient funds." /></label>
      {error && <p className="mt-3 rounded-lg bg-red-50 p-3 text-xs text-red-800" role="alert">{error}</p>}
      <div className="mt-6 flex justify-end gap-2"><button type="button" className="secondary" onClick={onClose}>Cancel</button><button className="primary" disabled={submitting || reason.trim().length < 8}><Icon name="refresh" className="size-4" />{submitting ? "Creating attempt…" : "Create new attempt"}</button></div>
    </form>
  </div>;
}

export default function PaymentDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<Timeline>();
  const [error, setError] = useState("");
  const [showRetry, setShowRetry] = useState(false);
  const load = useCallback(async () => { try { setError(""); setData(await api<Timeline>(`/payments/${id}/timeline`)); } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load payment"); } }, [id]);
  useEffect(() => {
    let active = true;
    api<Timeline>(`/payments/${id}/timeline`).then(result => { if (active) setData(result); }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : "Could not load payment"); });
    return () => { active = false; };
  }, [id]);

  const events = useMemo<EvidenceEvent[]>(() => {
    if (!data) return [];
    return [
      ...data.attempts.map(row => ({ id: `attempt-${row.id}`, category: `STK attempt ${row.attempt_number}`, title: human(row.status), description: row.response_description || row.checkout_request_id || "STK request created", timestamp: row.created_at, status: row.status })),
      ...data.callbacks.map(row => ({ id: `callback-${row.id}`, category: "Daraja callback", title: row.duplicate_of_callback_id ? "Duplicate preserved" : human(row.processing_status), description: row.result_description || row.mpesa_receipt_number || "Callback received", timestamp: row.received_at, status: row.result_code === "0" ? "success" : row.duplicate_of_callback_id ? "unknown" : "failed" })),
      ...data.status_checks.map(row => ({ id: `check-${row.id}`, category: "Status query", title: human(row.outcome), description: row.result_description || `Result code ${row.result_code || "not supplied"}`, timestamp: row.checked_at, status: row.outcome })),
      ...data.ledger.map(row => ({ id: `ledger-${row.id}`, category: "Ledger", title: human(row.event_type), description: row.status_from && row.status_to ? `${human(row.status_from)} → ${human(row.status_to)}` : "Audited payment event", timestamp: row.created_at, status: row.status_to }))
    ].sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime());
  }, [data]);

  if (error && !data) return <div role="alert" className="surface mx-auto max-w-xl p-8 text-center"><h2 className="text-xl font-bold">Payment evidence unavailable</h2><p className="mt-2 text-sm text-red-700">{error}</p><div className="mt-5 flex justify-center gap-2"><Link href="/payments" className="secondary no-underline">Back to payments</Link><button className="primary" onClick={() => void load()}>Try again</button></div></div>;
  if (!data) return <div className="space-y-4" aria-label="Loading payment"><div className="skeleton h-5 w-32 rounded"/><div className="skeleton h-12 max-w-xl rounded-xl"/><div className="grid gap-3 md:grid-cols-4">{Array.from({ length: 4 }, (_, index) => <div className="skeleton h-32 rounded-2xl" key={index}/>)}</div></div>;

  const payment = data.payment;
  const retryable = canRetryPayment({ status: payment.status, provider_acceptance_state: payment.provider_acceptance_state || "", receipt_status: payment.receipt_status, mpesa_receipt_number: payment.mpesa_receipt_number });
  return <section>
    <Link href="/payments" className="mb-5 inline-flex items-center gap-2 text-xs font-bold text-[#607168] no-underline hover:text-[#087448]"><Icon name="arrow" className="size-3.5 rotate-180"/>Back to payments</Link>
    <PageHeader eyebrow="Payment evidence" title={payment.external_reference} description={payment.description || "M-PESA payment evidence and auditable state history."} action={<div className="flex items-center gap-2"><StatusPill status={payment.status}/>{retryable && <button className="secondary" onClick={() => setShowRetry(true)}><Icon name="refresh" className="size-4"/>Retry safely</button>}</div>} />
    {error && <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
    <div className="my-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Amount" value={`KES ${Number(payment.amount).toLocaleString("en-KE")}`} detail={payment.customer_name || payment.customer_phone} icon="payments" />
      <MetricCard label="Success source" value={human(payment.success_source)} detail="Evidence authority" icon="shield" tone={payment.status === "success" ? "positive" : "neutral"} />
      <MetricCard label="Receipt evidence" value={human(payment.receipt_status)} detail={payment.mpesa_receipt_number || "No M-PESA receipt recorded"} icon="check" tone={payment.mpesa_receipt_number ? "positive" : "neutral"} />
      <MetricCard label="Provider acceptance" value={human(payment.provider_acceptance_state)} detail={`${data.attempts.length} STK attempt${data.attempts.length === 1 ? "" : "s"}`} icon="reconcile" />
    </div>

    {payment.review_status === "needs_review" && <div className="mb-5 flex gap-3 rounded-2xl border border-amber-200 bg-amber-50 p-4 text-amber-950"><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-amber-100"><Icon name="review" className="size-5"/></span><div><StatusPill status="needs_review"/><p className="mt-2 text-sm leading-6">{payment.review_reason || "This payment has incomplete or conflicting provider evidence and requires review."}</p></div></div>}

    <div className="grid gap-5 xl:grid-cols-[minmax(0,1.45fr)_minmax(300px,.55fr)]">
      <article className="surface overflow-hidden"><div className="flex items-center justify-between border-b border-[#e7ece8] px-5 py-4"><div><h3 className="text-sm font-bold">Evidence timeline</h3><p className="mt-1 text-[11px] text-[#7b8a82]">Newest event first</p></div><span className="rounded-full bg-[#eef2ef] px-2.5 py-1 text-[10px] font-bold text-[#607168]">{events.length} events</span></div><ol className="divide-y divide-[#edf1ee]">{events.map(event => <li className="grid grid-cols-[32px_minmax(0,1fr)_auto] gap-3 px-5 py-4" key={event.id}><span className="mt-0.5 grid size-8 place-items-center rounded-xl bg-[#eef7f1] text-[#087448]"><Icon name={event.category === "Daraja callback" ? "webhook" : event.category === "Ledger" ? "audit" : event.category === "Status query" ? "reconcile" : "payments"} className="size-4"/></span><div className="min-w-0"><p className="text-[10px] font-black uppercase tracking-[.1em] text-[#87968e]">{event.category}</p><div className="mt-1 flex flex-wrap items-center gap-2"><strong className="text-sm capitalize">{event.title}</strong>{event.status && <StatusPill status={event.status}/>}</div><p className="mt-1 truncate text-xs text-[#6d7c74]" title={event.description}>{event.description}</p></div><time className="whitespace-nowrap pt-0.5 text-[10px] text-[#8a9891]" dateTime={event.timestamp}>{formatDate(event.timestamp)}</time></li>)}</ol></article>
      <aside className="space-y-4"><div className="surface p-5"><h3 className="text-sm font-bold">Payment identifiers</h3><dl className="mt-4 space-y-4 text-xs">{[["Payment ID", payment.id], ["M-PESA receipt", payment.mpesa_receipt_number], ["Checkout request", payment.checkout_request_id]].map(([label, value]) => <div key={label}><dt className="font-semibold text-[#87968e]">{label}</dt><dd className="mt-1 break-all font-mono text-[11px] font-semibold text-[#33443b]">{value || "Not assigned"}</dd></div>)}</dl></div><div className="rounded-2xl border border-emerald-200 bg-emerald-50/70 p-5"><div className="flex items-center gap-2 text-xs font-black uppercase tracking-[.1em] text-emerald-800"><Icon name="shield" className="size-4"/>Evidence policy</div><p className="mt-3 text-xs leading-6 text-emerald-950/70">STK acceptance confirms request delivery—not payment. Success requires a valid callback or verified transaction status.</p></div></aside>
    </div>
    {showRetry && (
      <RetryDialog payment={payment} onClose={() => setShowRetry(false)} onComplete={load}/>
    )}
  </section>;
}
