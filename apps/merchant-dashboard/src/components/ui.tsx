import type { ReactNode } from "react";
import { Icon, type IconName } from "./icons";

export function PageHeader({ eyebrow, title, description, action }: { eyebrow: string; title: string; description: string; action?: ReactNode }) {
  return <div className="flex flex-wrap items-end justify-between gap-5">
    <div><p className="eyeline">{eyebrow}</p><h2 className="page-title">{title}</h2><p className="page-description">{description}</p></div>
    {action && <div className="shrink-0">{action}</div>}
  </div>;
}

export function MetricCard({ label, value, detail, icon, tone = "neutral" }: { label: string; value: string; detail: string; icon: IconName; tone?: "neutral" | "positive" | "warning" }) {
  const tones = { neutral: "bg-[#eef2ef] text-[#52635a]", positive: "bg-emerald-50 text-emerald-700", warning: "bg-amber-50 text-amber-700" };
  return <article className="surface relative overflow-hidden p-5 md:p-6">
    <div className="flex items-start justify-between gap-4"><p className="text-xs font-bold text-[#607168]">{label}</p><span className={`grid size-9 place-items-center rounded-xl ${tones[tone]}`}><Icon name={icon} className="size-[18px]" /></span></div>
    <strong className="mt-5 block text-[clamp(1.6rem,3vw,2.25rem)] font-[720] tracking-[-.045em]">{value}</strong>
    <p className="mt-2 text-xs leading-5 text-[#7b8a82]">{detail}</p>
  </article>;
}

export function EmptyState({ icon, title, description, action }: { icon: IconName; title: string; description: string; action?: ReactNode }) {
  return <div className="surface grid min-h-72 place-items-center p-8 text-center"><div className="max-w-sm"><span className="mx-auto grid size-12 place-items-center rounded-2xl bg-emerald-50 text-emerald-700"><Icon name={icon} className="size-6" /></span><h3 className="mt-4 text-base font-bold">{title}</h3><p className="mt-2 text-sm leading-6 text-[#6d7c74]">{description}</p>{action && <div className="mt-5">{action}</div>}</div></div>;
}

export function LoadingTable() {
  return <div className="surface overflow-hidden" aria-label="Loading payments"><div className="border-b border-[#e6ebe7] px-5 py-4"><div className="skeleton h-3 w-48 rounded" /></div>{Array.from({ length: 5 }, (_, index) => <div className="grid grid-cols-[1.5fr_.7fr_.7fr_1fr] gap-6 border-b border-[#edf1ee] px-5 py-5 last:border-0" key={index}><div><div className="skeleton h-3 w-32 rounded"/><div className="skeleton mt-2 h-2.5 w-24 rounded"/></div><div className="skeleton h-6 w-20 rounded-full"/><div className="skeleton h-3 w-24 rounded"/><div className="skeleton h-3 w-full rounded"/></div>)}</div>;
}
