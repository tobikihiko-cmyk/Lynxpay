"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/icons";
import { StatusPill } from "@/components/status-pill";
import { EmptyState, PageHeader } from "@/components/ui";

type Merchant = { id: string; merchant_name: string; organization_name: string; shortcode: string; shortcode_type?: string; status: string };

export default function Approvals() {
  const [items, setItems] = useState<Merchant[]>([]);
  const [denied, setDenied] = useState(false);
  const [busy, setBusy] = useState<string>();
  async function load() { setItems((await api<{ items: Merchant[] }>("/admin/merchants/pending-approval")).items); }
  useEffect(() => { let active = true; api<{ items: Merchant[] }>("/admin/merchants/pending-approval").then(result => { if (active) setItems(result.items); }).catch(() => { if (active) setDenied(true); }); return () => { active = false; }; }, []);
  async function approve(id: string) { setBusy(id); try { await api(`/admin/merchants/${id}/approve`, { method: "POST", body: JSON.stringify({ reason: "Production evidence reviewed by platform operations" }) }); await load(); } finally { setBusy(undefined); } }
  if (denied) return <section><PageHeader eyebrow="Platform operations" title="Restricted workspace" description="Production approval requires an independent LynxPay platform administrator. Merchant organization administrators cannot approve themselves."/><div className="surface mt-7 flex gap-4 p-6"><span className="grid size-11 shrink-0 place-items-center rounded-xl bg-amber-50 text-amber-700"><Icon name="shield" className="size-5"/></span><div><h3 className="font-bold">Independent approval enforced</h3><p className="mt-2 text-sm leading-6 text-[#607168]">This boundary protects live M-PESA credentials and prevents self-activation.</p></div></div></section>;
  return <section><PageHeader eyebrow="Live money gate" title="Production approvals" description="Review merchant ownership, Daraja credential validation, legal consent, and KES 1 callback proof before enabling production traffic."/><div className="mt-7 grid gap-4">{items.length ? items.map(merchant => <article className="surface flex flex-wrap items-center justify-between gap-5 p-5" key={merchant.id}><div className="flex items-center gap-3"><span className="grid size-11 place-items-center rounded-xl bg-[#eef7f1] text-sm font-black text-[#087448]">{merchant.merchant_name.slice(0,2).toUpperCase()}</span><div><h3 className="font-bold">{merchant.merchant_name}</h3><p className="mt-1 text-xs text-[#6d7c74]">{merchant.organization_name} · {merchant.shortcode_type || "PayBill"} {merchant.shortcode}</p></div></div><div className="flex items-center gap-3"><StatusPill status={merchant.status}/><button className="primary" disabled={busy === merchant.id} onClick={() => void approve(merchant.id)}><Icon name="check" className="size-4" />{busy === merchant.id ? "Approving…" : "Approve production"}</button></div></article>) : <EmptyState icon="approval" title="Approval queue is clear" description="No merchants are currently waiting for independent production review." />}</div></section>;
}
