"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { EmptyState, PageHeader } from "@/components/ui";
import { StatusPill } from "@/components/status-pill";

type Callback = {
  id: string; payment_id?: string; checkout_request_id?: string; mpesa_receipt_number?: string;
  result_code?: string; result_description?: string; processing_status: string; received_at: string;
};

export default function CallbacksPage() {
  const [items, setItems] = useState<Callback[]>([]);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    setError("");
    try {
      const query = status ? `?processing_status=${encodeURIComponent(status)}` : "";
      setItems((await api<{ items: Callback[] }>(`/callbacks${query}`)).items);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load callback evidence"); }
  }, [status]);
  useEffect(() => {
    let active = true;
    const query = status ? `?processing_status=${encodeURIComponent(status)}` : "";
    api<{ items: Callback[] }>(`/callbacks${query}`).then(result => { if (active) setItems(result.items); }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : "Could not load callback evidence"); });
    return () => { active = false; };
  }, [status]);
  return <section>
    <PageHeader eyebrow="Provider evidence" title="M-PESA callbacks" description="Every raw provider message is durably preserved before validation. Investigate duplicates, unmatched evidence, verification failures, and final outcomes here." />
    <div className="mt-6 flex flex-wrap items-end gap-3"><label className="field w-64">Processing state<select value={status} onChange={event => setStatus(event.target.value)}><option value="">All callback states</option>{["processed_success","processed_failure","processed_unknown","duplicate","unmatched","verification_failed","malformed","source_rejected"].map(value => <option value={value} key={value}>{value.replaceAll("_", " ")}</option>)}</select></label><button className="secondary" onClick={() => void load()}>Refresh evidence</button></div>
    {error && <p className="mt-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800" role="alert">{error}</p>}
    <div className="mt-6">{items.length ? <div className="surface overflow-x-auto"><table className="w-full min-w-[900px] text-left text-xs"><thead className="border-b border-[#e7ece8] bg-[#f8faf8] text-[10px] uppercase tracking-[.1em] text-[#75857c]"><tr>{["Received","State","Checkout request","Receipt","Result","Payment"].map(label => <th className="px-5 py-3" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-[#edf1ee]">{items.map(item => <tr key={item.id}><td className="px-5 py-4">{new Date(item.received_at).toLocaleString("en-KE")}</td><td className="px-5 py-4"><StatusPill status={item.processing_status}/></td><td className="px-5 py-4 font-mono text-[10px]">{item.checkout_request_id || "—"}</td><td className="px-5 py-4 font-mono">{item.mpesa_receipt_number || "—"}</td><td className="max-w-64 px-5 py-4"><strong>{item.result_code ?? "—"}</strong><span className="ml-2 text-[#77867e]">{item.result_description}</span></td><td className="px-5 py-4">{item.payment_id ? <Link className="font-bold text-emerald-700" href={`/payments/${item.payment_id}`}>View payment</Link> : <span className="font-bold text-amber-700">Needs linking review</span>}</td></tr>)}</tbody></table></div> : <EmptyState icon="callbacks" title="No callback evidence" description="M-PESA callbacks will appear here as soon as Safaricom reaches a merchant callback URL." />}</div>
  </section>;
}
