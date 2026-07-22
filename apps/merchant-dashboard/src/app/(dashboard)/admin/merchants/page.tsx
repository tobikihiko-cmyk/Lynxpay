"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/icons";
import { StatusPill } from "@/components/status-pill";
import { EmptyState, PageHeader } from "@/components/ui";

type Merchant = { id: string; merchant_name: string; organization_name: string; shortcode: string; shortcode_type?: string; status: string };

export default function Approvals() {
  const [items, setItems] = useState<Merchant[]>([]);
  const [activeItems, setActiveItems] = useState<Merchant[]>([]);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState<string>();
  async function load() {
    const [pending, active] = await Promise.all([
      api<{ items: Merchant[] }>("/admin/merchants/pending-approval"),
      api<{ items: Merchant[] }>("/admin/merchants?status=active"),
    ]);
    setItems(pending.items);
    setActiveItems(active.items);
  }
  useEffect(() => { let active = true; Promise.all([api<{ items: Merchant[] }>("/admin/merchants/pending-approval"), api<{ items: Merchant[] }>("/admin/merchants?status=active")]).then(([pending, live]) => { if (active) { setItems(pending.items); setActiveItems(live.items); } }).catch(() => { if (active) setDenied(true); }); return () => { active = false; }; }, []);
  async function approve(id: string) { setBusy(id); try { await api(`/admin/merchants/${id}/approve`, { method: "POST", body: JSON.stringify({ reason: "Production evidence reviewed by platform operations" }) }); await load(); } finally { setBusy(undefined); } }
  async function reject(id: string) { setBusy(id); try { await api(`/admin/merchants/${id}/reject`, { method: "POST", body: JSON.stringify({ reason: "Production evidence requires merchant correction before activation" }) }); await load(); } finally { setBusy(undefined); } }
  async function suspend(id: string) { setBusy(id); try { await api(`/admin/merchants/${id}/suspend`, { method: "POST", body: JSON.stringify({ reason: "Platform operations suspended live payment initiation pending review" }) }); await load(); } finally { setBusy(undefined); } }
  if (denied) return <section><PageHeader eyebrow="Platform operations" title="Restricted workspace" description="Production approval requires an independent LynxPay platform administrator. Merchant organization administrators cannot approve themselves."/><div className="surface mt-7 flex gap-4 p-6"><span className="grid size-11 shrink-0 place-items-center rounded-xl bg-amber-50 text-amber-700"><Icon name="shield" className="size-5"/></span><div><h3 className="font-bold">Independent approval enforced</h3><p className="mt-2 text-sm leading-6 text-[#607168]">This boundary protects live M-PESA credentials and prevents self-activation.</p></div></div></section>;
  return <section><PageHeader eyebrow="Live money gate" title="Production approvals" description="Review merchant ownership, Daraja credential validation, legal consent, and KES 1 callback proof before enabling production traffic."/><div className="mt-7 grid gap-4">{items.length ? items.map(merchant => <article className="surface flex flex-wrap items-center justify-between gap-5 p-5" key={merchant.id}><div className="flex items-center gap-3"><span className="grid size-11 place-items-center rounded-xl bg-[#eef7f1] text-sm font-black text-[#087448]">{merchant.merchant_name.slice(0,2).toUpperCase()}</span><div><h3 className="font-bold">{merchant.merchant_name}</h3><p className="mt-1 text-xs text-[#6d7c74]">{merchant.organization_name} · {merchant.shortcode_type || "PayBill"} {merchant.shortcode}</p></div></div><div className="flex flex-wrap items-center gap-3"><StatusPill status={merchant.status}/><button className="secondary text-red-700" disabled={busy === merchant.id} onClick={() => void reject(merchant.id)}>Reject</button><button className="primary" disabled={busy === merchant.id} onClick={() => void approve(merchant.id)}><Icon name="check" className="size-4" />{busy === merchant.id ? "Reviewing…" : "Approve production"}</button></div></article>) : <EmptyState icon="approval" title="Approval queue is clear" description="No merchants are currently waiting for independent production review." />}</div><div className="mt-10 flex items-end justify-between gap-4"><div><p className="eyebrow">Active estate</p><h2 className="mt-2 text-xl font-black tracking-tight">Live merchant controls</h2></div><span className="text-sm font-semibold text-[#607168]">{activeItems.length} active</span></div><div className="mt-4 grid gap-4">{activeItems.length ? activeItems.map(merchant => <article className="surface flex flex-wrap items-center justify-between gap-5 p-5" key={merchant.id}><div><h3 className="font-bold">{merchant.merchant_name}</h3><p className="mt-1 text-xs text-[#6d7c74]">{merchant.organization_name} · {merchant.shortcode_type || "PayBill"} {merchant.shortcode}</p></div><div className="flex items-center gap-3"><StatusPill status={merchant.status}/><button className="secondary text-red-700" disabled={busy === merchant.id} onClick={() => void suspend(merchant.id)}>{busy === merchant.id ? "Suspending…" : "Suspend live traffic"}</button></div></article>) : <EmptyState icon="shield" title="No active production merchants" description="Approved production merchants will appear here for incident controls." />}</div></section>;
}
