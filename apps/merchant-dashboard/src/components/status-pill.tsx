const tones: Record<string, string> = {
  success: "bg-emerald-100 text-emerald-800", active: "bg-emerald-100 text-emerald-800",
  failed: "bg-red-100 text-red-800", rejected: "bg-red-100 text-red-800",
  unknown: "bg-amber-100 text-amber-900", timeout: "bg-amber-100 text-amber-900", needs_review: "bg-amber-100 text-amber-900",
  stk_sent: "bg-blue-100 text-blue-800", pending: "bg-blue-100 text-blue-800", pending_approval: "bg-blue-100 text-blue-800"
};

export function StatusPill({ status }: { status: string }) {
  return <span className={`inline-flex rounded-full px-2.5 py-1 text-[10px] font-extrabold uppercase tracking-wider ${tones[status] || "bg-slate-100 text-slate-700"}`}>{status.replaceAll("_", " ")}</span>;
}
