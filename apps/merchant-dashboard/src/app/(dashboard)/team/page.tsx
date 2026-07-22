"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui";
import { StatusPill } from "@/components/status-pill";

type Member = { id: string; full_name: string; email: string; role: string; status: string };
type Me = { mfa_enabled: boolean; mfa_authenticated: boolean };

export default function TeamPage() {
  const [members, setMembers] = useState<Member[]>([]); const [me, setMe] = useState<Me>();
  const [mfa, setMfa] = useState<{ secret: string; provisioning_uri: string }>(); const [code, setCode] = useState(""); const [message, setMessage] = useState("");
  useEffect(() => { void Promise.all([api<{items: Member[]}>("/team/users").then(x => setMembers(x.items)), api<Me>("/auth/me").then(setMe)]).catch(caught => setMessage(caught instanceof Error ? caught.message : "Could not load security workspace")); }, []);
  async function setupMfa() { const result = await api<{secret: string; provisioning_uri: string}>("/auth/mfa/setup", { method: "POST" }); setMfa(result); }
  async function confirmMfa() { await api("/auth/mfa/confirm", { method: "POST", body: JSON.stringify({ code }) }); setMfa(undefined); setMe({ mfa_enabled: true, mfa_authenticated: true }); setMessage("MFA is enabled for privileged operations."); }
  return <section><PageHeader eyebrow="Access assurance" title="Team & MFA" description="Assign least-privilege operational roles and protect credential, API-key, callback-evidence, and production approval actions with MFA."/>
    {message && <p role="status" className="mt-5 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-900">{message}</p>}
    <div className="mt-7 grid gap-5 xl:grid-cols-[1.25fr_.75fr]"><div className="surface overflow-hidden"><div className="border-b border-[#edf1ee] p-5"><h3 className="font-bold">Organization access</h3><p className="mt-1 text-xs text-[#6d7c74]">Owner · admin · operator · developer · support · accountant · read only</p></div><div className="divide-y divide-[#edf1ee]">{members.map(member => <div className="flex items-center justify-between gap-4 p-5" key={member.id}><div><strong className="text-sm">{member.full_name}</strong><p className="mt-1 text-xs text-[#74837b]">{member.email} · <span className="capitalize">{member.role.replaceAll("_", " ")}</span></p></div><StatusPill status={member.status}/></div>)}</div></div>
      <div className="surface p-6"><p className="eyeline">Privileged session</p><h3 className="mt-2 text-xl font-bold">Multi-factor authentication</h3><p className="mt-2 text-sm leading-6 text-[#65756d]">Production control-plane actions require a recent MFA-authenticated session.</p><div className="mt-5"><StatusPill status={me?.mfa_enabled ? "enabled" : "not_enabled"}/></div>{!me?.mfa_enabled && !mfa && <button className="primary mt-5" onClick={() => void setupMfa()}>Set up authenticator</button>}{mfa && <div className="mt-5 grid gap-4"><label className="field">Authenticator secret<input readOnly value={mfa.secret}/></label><label className="field">Six-digit code<input inputMode="numeric" autoComplete="one-time-code" value={code} onChange={event => setCode(event.target.value)}/></label><button className="primary" disabled={code.length < 6} onClick={() => void confirmMfa()}>Confirm MFA</button></div>}</div></div>
  </section>;
}
