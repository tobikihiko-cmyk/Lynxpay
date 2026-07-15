const tones: Record<string, string> = {
  success: "border-emerald-200 bg-emerald-50 text-emerald-800 before:bg-emerald-500",
  active: "border-emerald-200 bg-emerald-50 text-emerald-800 before:bg-emerald-500",
  failed: "border-red-200 bg-red-50 text-red-800 before:bg-red-500",
  rejected: "border-red-200 bg-red-50 text-red-800 before:bg-red-500",
  suspended: "border-red-200 bg-red-50 text-red-800 before:bg-red-500",
  unknown: "border-amber-200 bg-amber-50 text-amber-900 before:bg-amber-500",
  timeout: "border-amber-200 bg-amber-50 text-amber-900 before:bg-amber-500",
  needs_review: "border-amber-200 bg-amber-50 text-amber-900 before:bg-amber-500",
  stk_sent: "border-blue-200 bg-blue-50 text-blue-800 before:bg-blue-500",
  pending: "border-blue-200 bg-blue-50 text-blue-800 before:bg-blue-500",
  pending_approval: "border-violet-200 bg-violet-50 text-violet-800 before:bg-violet-500",
  created: "border-slate-200 bg-slate-50 text-slate-700 before:bg-slate-400",
  inactive: "border-slate-200 bg-slate-50 text-slate-600 before:bg-slate-400"
};

export function StatusPill({ status }: { status: string }) {
  return <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-1 text-[9px] font-black uppercase tracking-[.09em] before:size-1.5 before:rounded-full ${tones[status] || "border-slate-200 bg-slate-50 text-slate-700 before:bg-slate-400"}`}>{status.replaceAll("_", " ")}</span>;
}
