"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon } from "@/components/icons";
import { StatusPill } from "@/components/status-pill";
import { EmptyState, PageHeader } from "@/components/ui";

type Key = { id: string; name: string; key_prefix: string; environment: string; status: string; scopes?: string[]; last_used_at?: string };

export default function ApiKeys() {
  const [items, setItems] = useState<Key[]>([]);
  const [secret, setSecret] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  async function load() { const result = await api<{ items: Key[] }>("/api-keys"); setItems(result.items); }
  useEffect(() => { let active = true; api<{ items: Key[] }>("/api-keys").then(result => { if (active) setItems(result.items); }).catch(caught => { if (active) setError(caught instanceof Error ? caught.message : "Could not load API keys"); }); return () => { active = false; }; }, []);

  async function create() {
    setCreating(true); setError("");
    try {
      const result = await api<{ api_key: string }>("/api-keys", { method: "POST", body: JSON.stringify({ name: "Dashboard integration key", environment: "sandbox", scopes: ["payments:read", "payments:write", "callbacks:read", "webhooks:read"] }) });
      setSecret(result.api_key); await load();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not create API key"); }
    finally { setCreating(false); }
  }

  return <section>
    <PageHeader eyebrow="Developer access" title="API keys" description="Merchant-scoped credentials for trusted server integrations. Full keys are shown once and stored by LynxPay only as secure hashes." action={<button className="primary" onClick={create} disabled={creating}><Icon name="key" className="size-4" />{creating ? "Creating…" : "Create sandbox key"}</button>} />
    {error && <p role="alert" className="mt-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
    {secret && <div className="my-6 rounded-2xl border border-amber-200 bg-amber-50 p-5"><div className="flex items-center gap-2 text-sm font-bold text-amber-950"><Icon name="review" className="size-4" />Copy this key now. It will not be shown again.</div><div className="mt-4 flex gap-2"><code className="min-w-0 flex-1 overflow-x-auto rounded-xl bg-[#07150e] p-4 text-xs text-emerald-200">{secret}</code><button className="secondary shrink-0" onClick={() => void navigator.clipboard.writeText(secret)}>Copy</button></div></div>}
    <div className="mt-7">{items.length ? <div className="surface divide-y divide-[#edf1ee]">{items.map(key => <div className="flex flex-wrap items-center justify-between gap-4 p-5" key={key.id}><div className="flex items-center gap-3"><span className="grid size-10 place-items-center rounded-xl bg-[#eef7f1] text-[#087448]"><Icon name="key" className="size-5" /></span><div><strong className="text-sm">{key.name}</strong><p className="mt-1 font-mono text-[10px] text-[#7b8a82]">{key.key_prefix}•••• · {key.environment}</p></div></div><div className="flex items-center gap-3"><span className="hidden text-[10px] text-[#87968e] sm:block">{key.last_used_at ? `Last used ${new Date(key.last_used_at).toLocaleDateString("en-KE")}` : "Never used"}</span><StatusPill status={key.status}/></div></div>)}</div> : <EmptyState icon="key" title="No API keys yet" description="Create a sandbox key when your server integration is ready. Never place a LynxPay API key in browser code." />}</div>
  </section>;
}
