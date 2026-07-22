"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/icons";
import { StatusPill } from "@/components/status-pill";
import { EmptyState, PageHeader } from "@/components/ui";

type Endpoint = { id: string; url: string; status: string; event_types: string[]; consecutive_failures?: number; pause_reason?: string };
type Delivery = { id: string; endpoint_id: string; event_type: string; status: string; attempts: number; max_attempts: number; response_status_code?: number; last_error?: string; created_at: string };

export default function Webhooks() {
  const [items, setItems] = useState<Endpoint[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  async function load() { const [endpointResult, deliveryResult] = await Promise.all([api<{ items: Endpoint[] }>("/webhooks/endpoints"), api<{ items: Delivery[] }>("/webhooks/deliveries?limit=100")]); setItems(endpointResult.items); setDeliveries(deliveryResult.items); }
  useEffect(() => { let active = true; Promise.all([api<{ items: Endpoint[] }>("/webhooks/endpoints"), api<{ items: Delivery[] }>("/webhooks/deliveries?limit=100")]).then(([endpointResult, deliveryResult]) => { if (active) { setItems(endpointResult.items); setDeliveries(deliveryResult.items); } }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : "Could not load webhooks"); }); return () => { active = false; }; }, []);
  async function replay(id: string) { setError(""); try { await api(`/webhooks/deliveries/${id}/replay`, { method: "POST" }); await load(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not replay delivery"); } }
  async function create(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSubmitting(true); setError("");
    const form = event.currentTarget; const data = new FormData(form);
    try { await api("/webhooks/endpoints", { method: "POST", body: JSON.stringify({ url: data.get("url"), event_types: ["payment.success", "payment.failed"] }) }); form.reset(); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not create endpoint"); }
    finally { setSubmitting(false); }
  }
  return <section>
    <PageHeader eyebrow="Signed delivery" title="Webhooks" description="Deliver verified payment state to your systems with HMAC signatures, durable retries, and a replayable delivery history." />
    {error && <p role="alert" className="mt-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
    <div className="mt-7 grid gap-5 xl:grid-cols-[.72fr_1.28fr]">
      <form className="surface grid content-start gap-4 p-6" onSubmit={create}><span className="grid size-10 place-items-center rounded-xl bg-emerald-50 text-emerald-700"><Icon name="webhook" className="size-5" /></span><div><h3 className="font-bold">Connect an endpoint</h3><p className="mt-1 text-xs leading-5 text-[#6d7c74]">We require HTTPS for production endpoints.</p></div><label className="field">Endpoint URL<input name="url" type="url" required placeholder="https://merchant.co.ke/webhooks/lynxpay" /></label><div className="rounded-xl bg-[#f3f6f4] p-3 text-[11px] leading-5 text-[#607168]"><strong className="block text-[#33443b]">Initial subscriptions</strong>payment.success · payment.failed</div><button className="primary" disabled={submitting}>{submitting ? "Connecting…" : "Create endpoint"}</button>
      </form>
      {items.length ? <div className="surface divide-y divide-[#edf1ee] self-start">{items.map(endpoint => <article className="p-5" key={endpoint.id}><div className="flex items-start justify-between gap-4"><div className="flex min-w-0 gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[#eef7f1] text-[#087448]"><Icon name="webhook" className="size-4"/></span><div className="min-w-0"><code className="block truncate text-xs font-bold" title={endpoint.url}>{endpoint.url}</code><p className="mt-2 text-[10px] text-[#7b8a82]">{endpoint.event_types.join(" · ")}</p>{Boolean(endpoint.consecutive_failures) && <p className="mt-2 text-[10px] font-bold text-amber-700">{endpoint.consecutive_failures} consecutive failures {endpoint.pause_reason ? `· ${endpoint.pause_reason.replaceAll("_", " ")}` : ""}</p>}</div></div><StatusPill status={endpoint.status}/></div></article>)}</div> : <EmptyState icon="webhook" title="No webhook endpoints" description="Connect your backend to receive signed payment success and failure events from LynxPay." />}
    </div>
    <div className="mt-8"><div className="mb-4"><p className="eyeline">Delivery operations</p><h3 className="mt-2 text-2xl font-bold">Attempts, failures and replay</h3></div>{deliveries.length ? <div className="surface overflow-x-auto"><table className="w-full min-w-[850px] text-left text-xs"><thead className="border-b border-[#e7ece8] bg-[#f8faf8] text-[10px] uppercase tracking-[.1em] text-[#75857c]"><tr>{["Created","Event","State","Attempts","Response","Action"].map(label => <th className="px-5 py-3" key={label}>{label}</th>)}</tr></thead><tbody className="divide-y divide-[#edf1ee]">{deliveries.map(delivery => <tr key={delivery.id}><td className="px-5 py-4">{new Date(delivery.created_at).toLocaleString("en-KE")}</td><td className="px-5 py-4 font-mono text-[10px]">{delivery.event_type}</td><td className="px-5 py-4"><StatusPill status={delivery.status}/></td><td className="px-5 py-4">{delivery.attempts}/{delivery.max_attempts}</td><td className="max-w-72 px-5 py-4 text-[#66766e]">{delivery.response_status_code || delivery.last_error || "Awaiting delivery"}</td><td className="px-5 py-4">{["dead_letter","failed"].includes(delivery.status) ? <button className="secondary" onClick={() => void replay(delivery.id)}>Replay</button> : "—"}</td></tr>)}</tbody></table></div> : <EmptyState icon="webhook" title="No webhook deliveries" description="Delivery attempts will appear after a subscribed payment event is queued." />}</div>
  </section>;
}
