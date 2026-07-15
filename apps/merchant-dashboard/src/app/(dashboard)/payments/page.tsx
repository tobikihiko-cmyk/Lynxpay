"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { Payment, PaymentTable } from "@/components/payment-table";
import { Icon } from "@/components/icons";
import { LoadingTable, MetricCard, PageHeader } from "@/components/ui";

type PaymentPage = { items: Payment[]; next_before?: string | null };
const statuses = ["success", "stk_sent", "pending", "unknown", "failed", "timeout"];

export default function PaymentsPage() {
  const [items, setItems] = useState<Payment[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [reviewOnly, setReviewOnly] = useState(false);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async ({ append = false, before }: { append?: boolean; before?: string } = {}) => {
    if (append) setLoadingMore(true); else setLoading(true);
    setError("");
    try {
      const query = new URLSearchParams({ limit: "50" });
      if (search.trim()) query.set("search", search.trim());
      if (status) query.set("status", status);
      if (reviewOnly) query.set("review_status", "needs_review");
      if (before) query.set("before", before);
      const result = await api<PaymentPage>(`/payments?${query}`);
      setItems(current => append ? [...current, ...result.items] : result.items);
      setNextBefore(result.next_before || null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load payments");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [reviewOnly, search, status]);

  useEffect(() => {
    let active = true;
    api<PaymentPage>("/payments?limit=50").then(result => {
      if (!active) return;
      setItems(result.items);
      setNextBefore(result.next_before || null);
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "Could not load payments");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const summary = useMemo(() => ({
    captured: items.filter(item => item.status === "success").reduce((total, item) => total + Number(item.amount), 0),
    awaiting: items.filter(item => ["created", "pending", "stk_sent", "unknown"].includes(item.status)).length,
    review: items.filter(item => item.review_status === "needs_review").length,
    success: items.length ? Math.round(items.filter(item => item.status === "success").length / items.length * 100) : 0
  }), [items]);

  return <section>
    <PageHeader eyebrow="Money movement" title="M-PESA payments" description="A live operational ledger built from callback and status-query evidence. Daraja acceptance alone never marks a payment successful." action={<button className="secondary" onClick={() => void load()} disabled={loading}><Icon name="refresh" className="size-4" />Refresh evidence</button>} />

    <div className="my-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Captured volume" value={`KES ${summary.captured.toLocaleString("en-KE")}`} detail="Successful payments in this view" icon="check" tone="positive" />
      <MetricCard label="Awaiting evidence" value={String(summary.awaiting)} detail="Pending callback or verification" icon="clock" />
      <MetricCard label="Needs review" value={String(summary.review)} detail="Conflicting or incomplete evidence" icon="review" tone={summary.review ? "warning" : "neutral"} />
      <MetricCard label="Success rate" value={`${summary.success}%`} detail="Across the loaded payment window" icon="payments" />
    </div>

    <div className="mb-4 rounded-2xl border border-[#dfe6e1] bg-white p-3 shadow-[0_1px_2px_rgb(7_21_14/.03)]">
      <form onSubmit={event => { event.preventDefault(); void load(); }} className="flex flex-wrap items-center gap-2">
        <label className="relative min-w-[240px] flex-1"><span className="sr-only">Search payments</span><Icon name="search" className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-[#8a9891]"/><input aria-label="Search payments" className="control pl-10 text-sm" placeholder="Receipt, phone, reference or checkout ID" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <label><span className="sr-only">Payment status</span><select aria-label="Payment status" className="control min-w-36 text-sm font-semibold" value={status} onChange={event => setStatus(event.target.value)}><option value="">All states</option>{statuses.map(value => <option value={value} key={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
        <label className={`flex min-h-[46px] cursor-pointer items-center gap-2 rounded-xl border px-3 text-xs font-bold transition ${reviewOnly ? "border-amber-300 bg-amber-50 text-amber-900" : "border-[#cbd6cf] bg-white text-[#52635a]"}`}><input type="checkbox" className="accent-amber-600" checked={reviewOnly} onChange={event => setReviewOnly(event.target.checked)} />Needs review</label>
        <button className="primary" type="submit" disabled={loading}>Apply filters</button>
      </form>
    </div>

    {error && <div role="alert" className="mb-4 flex items-start justify-between gap-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800"><p>{error}</p><button className="font-bold underline" onClick={() => void load()}>Try again</button></div>}
    {loading ? <LoadingTable /> : <PaymentTable payments={items} />}
    {!loading && nextBefore && <div className="mt-5 flex justify-center"><button className="secondary" disabled={loadingMore} onClick={() => void load({ append: true, before: nextBefore })}>{loadingMore ? "Loading…" : "Load older payments"}</button></div>}
  </section>;
}
