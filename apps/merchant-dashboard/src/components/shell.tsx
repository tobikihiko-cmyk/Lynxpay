"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const navigation = [
  ["Payments", "/payments"], ["Reconciliation", "/reconciliation"], ["Onboarding", "/onboarding"],
  ["API keys", "/api-keys"], ["Webhooks", "/webhooks"], ["Audit trail", "/audit"], ["Production approvals", "/admin/merchants"]
];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname(); const router = useRouter();
  async function logout() { await fetch("/api/session/logout", { method: "POST" }); router.replace("/sign-in"); router.refresh(); }
  return <div className="min-h-screen lg:grid lg:grid-cols-[270px_1fr]">
    <aside className="bg-[#06130c] p-6 text-white lg:sticky lg:top-0 lg:h-screen">
      <Link href="/payments" className="flex items-center gap-3 no-underline"><span className="grid size-11 place-items-center rounded-xl bg-[#18c77a] font-black text-[#03140a]">LX</span><span><strong className="block text-lg">LynxPay</strong><small className="text-[9px] uppercase tracking-[.16em] text-emerald-100/45">Daraja control room</small></span></Link>
      <nav aria-label="Merchant dashboard" className="mt-12 grid gap-1">{navigation.map(([label, href]) => <Link key={href} href={href} className={`rounded-lg px-3 py-2.5 text-sm no-underline ${pathname.startsWith(href) ? "bg-emerald-400/15 text-emerald-50 ring-1 ring-emerald-400/25" : "text-emerald-50/55 hover:bg-white/5 hover:text-white"}`}>{label}</Link>)}</nav>
      <div className="mt-12 rounded-xl border border-emerald-300/10 bg-emerald-300/5 p-4"><p className="text-[10px] font-bold uppercase tracking-widest text-emerald-300">Safaricom Daraja</p><p className="mt-2 text-xs text-emerald-50/55">Accepted is not paid. Callback or verified status evidence decides success.</p></div>
      <button className="mt-6 text-xs text-white/45 hover:text-white" onClick={logout}>Sign out</button>
    </aside>
    <main className="min-w-0 px-5 pb-16 md:px-10 lg:px-14"><header className="flex min-h-28 items-center justify-between border-b border-[#dce3de]"><div><p className="eyeline">M-PESA OPERATIONS</p><h1 className="mt-2 text-3xl font-bold tracking-[-.04em]">Merchant workspace</h1></div><span className="hidden rounded-full bg-emerald-100 px-3 py-1.5 text-xs font-bold text-emerald-800 sm:inline">Secure session</span></header><div className="pt-8">{children}</div></main>
  </div>;
}
