"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Icon, type IconName } from "./icons";

type NavItem = { label: string; href: string; icon: IconName; group: "Operate" | "Configure" | "Platform" };
const navigation: NavItem[] = [
  { label: "Payments", href: "/payments", icon: "payments", group: "Operate" },
  { label: "Callbacks", href: "/callbacks", icon: "callbacks", group: "Operate" },
  { label: "Reconciliation", href: "/reconciliation", icon: "reconcile", group: "Operate" },
  { label: "Onboarding", href: "/onboarding", icon: "onboarding", group: "Configure" },
  { label: "API keys", href: "/api-keys", icon: "key", group: "Configure" },
  { label: "Webhooks", href: "/webhooks", icon: "webhook", group: "Configure" },
  { label: "Team & MFA", href: "/team", icon: "team", group: "Configure" },
  { label: "Audit trail", href: "/audit", icon: "audit", group: "Configure" },
  { label: "Approvals", href: "/admin/merchants", icon: "approval", group: "Platform" }
];

function Logo() {
  return <Link href="/payments" className="flex items-center gap-3 text-white no-underline" aria-label="LynxPay payments home">
    <span className="relative grid size-10 place-items-center overflow-hidden rounded-[13px] bg-[#20ce7f] text-[13px] font-black tracking-[-.08em] text-[#03140a] shadow-[0_10px_30px_rgb(25_200_120/.2)]"><span className="relative z-10">LX</span><span className="absolute -right-3 -top-3 size-7 rounded-full bg-white/25" /></span>
    <span><strong className="block text-[17px] font-[750] tracking-[-.035em]">LynxPay</strong><small className="mt-0.5 block text-[8px] font-bold uppercase tracking-[.2em] text-emerald-100/45">M-PESA infrastructure</small></span>
  </Link>;
}

type CurrentUser = { full_name: string; email: string; is_platform_admin: boolean };

function SideNavigation({ pathname, platformAdmin, onNavigate }: { pathname: string; platformAdmin: boolean; onNavigate?: () => void }) {
  return <nav aria-label="Merchant dashboard" className="mt-10 flex-1 overflow-y-auto">
    {(["Operate", "Configure", "Platform"] as const).map(group => <div className="mb-7" key={group}>
      <p className="mb-2 px-3 text-[9px] font-black uppercase tracking-[.2em] text-white/25">{group}</p>
      <div className="grid gap-1">{navigation.filter(item => item.group === group && (item.group !== "Platform" || platformAdmin)).map(item => {
        const active = pathname === item.href || pathname.startsWith(`${item.href}/`);
        return <Link key={item.href} href={item.href} onClick={onNavigate} aria-current={active ? "page" : undefined} className={`group flex min-h-10 items-center gap-3 rounded-xl px-3 text-[13px] font-semibold no-underline transition ${active ? "bg-emerald-400/[.12] text-emerald-50 ring-1 ring-inset ring-emerald-300/[.12]" : "text-emerald-50/50 hover:bg-white/[.045] hover:text-white"}`}>
          <Icon name={item.icon} className={`size-[17px] ${active ? "text-emerald-300" : "text-white/30 group-hover:text-white/60"}`} />{item.label}
          {item.label === "Reconciliation" && <span className="ml-auto size-1.5 rounded-full bg-amber-400" aria-label="Items need attention" />}
        </Link>;
      })}</div>
    </div>)}
  </nav>;
}

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [user, setUser] = useState<CurrentUser>();
  useEffect(() => { document.body.style.overflow = mobileOpen ? "hidden" : ""; return () => { document.body.style.overflow = ""; }; }, [mobileOpen]);
  useEffect(() => { let active = true; api<CurrentUser>("/auth/me").then(result => { if (active) setUser(result); }).catch(() => undefined); return () => { active = false; }; }, []);

  async function logout() {
    setLoggingOut(true);
    await fetch("/api/session/logout", { method: "POST" });
    router.replace("/sign-in");
    router.refresh();
  }

  const current = navigation.find(item => pathname === item.href || pathname.startsWith(`${item.href}/`));
  const sidebar = <>
    <div className="flex items-center justify-between"><Logo /><button type="button" className="grid size-10 place-items-center rounded-xl text-white/60 hover:bg-white/10 hover:text-white lg:hidden" onClick={() => setMobileOpen(false)} aria-label="Close navigation"><Icon name="close" className="size-5" /></button></div>
    <SideNavigation pathname={pathname} platformAdmin={Boolean(user?.is_platform_admin)} onNavigate={() => setMobileOpen(false)} />
    <div className="mt-3 rounded-2xl border border-emerald-300/[.09] bg-emerald-300/[.045] p-4">
      <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[.14em] text-emerald-300"><span className="relative flex size-2"><span className="absolute inline-flex size-full animate-ping rounded-full bg-emerald-400 opacity-50"/><span className="relative size-2 rounded-full bg-emerald-400"/></span>Daraja connected</div>
      <p className="mt-2.5 text-[11px] leading-[1.6] text-emerald-50/45">A payment is successful only after callback or verified status evidence.</p>
    </div>
    <button className="mt-3 flex min-h-10 w-full items-center gap-3 rounded-xl px-3 text-left text-xs font-semibold text-white/40 hover:bg-white/5 hover:text-white" onClick={logout} disabled={loggingOut}><span className="grid size-7 place-items-center rounded-lg bg-white/5 text-[10px] font-bold">{user?.full_name?.split(/\s+/).map(part => part[0]).join("").slice(0, 2).toUpperCase() || "LP"}</span><span className="min-w-0 flex-1"><span className="block truncate">{user?.full_name || "Merchant account"}</span><span className="block truncate text-[9px] font-medium text-white/25">{loggingOut ? "Signing out…" : user?.email || "Sign out"}</span></span><Icon name="arrow" className="size-4" /></button>
  </>;

  return <div className="min-h-screen lg:grid lg:grid-cols-[252px_minmax(0,1fr)]">
    <aside className="fixed inset-y-0 left-0 z-40 hidden w-[252px] flex-col bg-[#07150e] p-5 text-white lg:flex">{sidebar}</aside>
    {mobileOpen && <div className="fixed inset-0 z-50 lg:hidden"><button className="absolute inset-0 bg-[#041009]/70 backdrop-blur-sm" aria-label="Close navigation" onClick={() => setMobileOpen(false)} /><aside className="relative flex h-full w-[min(86vw,310px)] flex-col bg-[#07150e] p-5 shadow-2xl">{sidebar}</aside></div>}
    <main className="min-w-0 lg:col-start-2">
      <header className="sticky top-0 z-30 border-b border-[#dfe6e1]/90 bg-[#f4f6f3]/85 backdrop-blur-xl">
        <div className="mx-auto flex h-[72px] max-w-[1600px] items-center gap-3 px-4 sm:px-6 lg:px-10 xl:px-12">
          <button type="button" onClick={() => setMobileOpen(true)} className="grid size-10 place-items-center rounded-xl border border-[#dfe6e1] bg-white text-[#52635a] shadow-sm lg:hidden" aria-label="Open navigation"><Icon name="menu" className="size-5" /></button>
          <div className="min-w-0"><p className="truncate text-[10px] font-black uppercase tracking-[.16em] text-[#8a9891]">Merchant workspace</p><p className="mt-0.5 truncate text-sm font-bold tracking-[-.015em]">{current?.label || "LynxPay"}</p></div>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-[10px] font-extrabold uppercase tracking-[.08em] text-emerald-800 sm:inline-flex"><span className="size-1.5 rounded-full bg-emerald-500"/>Secure session</span>
            <button className="relative grid size-10 place-items-center rounded-xl border border-[#dfe6e1] bg-white text-[#52635a] shadow-sm hover:text-[#09140f]" aria-label="Notifications"><Icon name="bell" className="size-[18px]" /><span className="absolute right-2.5 top-2.5 size-1.5 rounded-full bg-amber-500 ring-2 ring-white" /></button>
          </div>
        </div>
      </header>
      <div className="mx-auto max-w-[1600px] px-4 pb-20 pt-8 sm:px-6 lg:px-10 lg:pt-10 xl:px-12">{children}</div>
    </main>
  </div>;
}
