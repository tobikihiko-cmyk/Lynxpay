"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/icons";
import { StatusPill } from "@/components/status-pill";
import { EmptyState, PageHeader } from "@/components/ui";

type Endpoint = { id: string; url: string; status: string; event_types: string[] };

export default function Webhooks() {
  const [items, setItems] = useState<Endpoint[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  async function load() { setItems((await api<{ items: Endpoint[] }>("/webhooks/endpoints")).items); }
  useEffect(() => { let active = true; api<{ items: Endpoint[] }>("/webhooks/endpoints").then(result => { if (active) setItems(result.items); }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : "Could not load webhooks"); }); return () => { active = false; }; }, []);
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
      {items.length ? <div className="surface divide-y divide-[#edf1ee] self-start">{items.map(endpoint => <article className="p-5" key={endpoint.id}><div className="flex items-start justify-between gap-4"><div className="flex min-w-0 gap-3"><span className="grid size-9 shrink-0 place-items-center rounded-xl bg-[#eef7f1] text-[#087448]"><Icon name="webhook" className="size-4"/></span><div className="min-w-0"><code className="block truncate text-xs font-bold" title={endpoint.url}>{endpoint.url}</code><p className="mt-2 text-[10px] text-[#7b8a82]">{endpoint.event_types.join(" · ")}</p></div></div><StatusPill status={endpoint.status}/></div></article>)}</div> : <EmptyState icon="webhook" title="No webhook endpoints" description="Connect your backend to receive signed payment success and failure events from LynxPay." />}
    </div>
  </section>;
}
