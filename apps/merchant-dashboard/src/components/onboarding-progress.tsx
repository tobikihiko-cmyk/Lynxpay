import { Icon, type IconName } from "./icons";
import type { OnboardingStep, OnboardingStepId } from "@/lib/onboarding";

const icons: Record<OnboardingStepId, IconName> = {
  account: "shield",
  business: "audit",
  mpesa: "payments",
  credentials: "key",
  verification: "reconcile",
  activation: "check"
};

export function OnboardingProgress({ steps, active, percentage, onSelect }: { steps: OnboardingStep[]; active: OnboardingStepId; percentage: number; onSelect: (step: OnboardingStepId) => void }) {
  return <aside className="surface overflow-hidden lg:sticky lg:top-28 lg:self-start">
    <div className="border-b border-[#e7ece8] p-5">
      <div className="flex items-center justify-between"><div><p className="eyeline">Setup progress</p><strong className="mt-1.5 block text-2xl tracking-[-.04em]">{percentage}%</strong></div><span className="grid size-11 place-items-center rounded-2xl bg-emerald-50 text-sm font-black text-emerald-700">{steps.filter(step => step.complete).length}/{steps.length}</span></div>
      <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-[#e9eeea]" role="progressbar" aria-label="Onboarding completion" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percentage}><div className="h-full rounded-full bg-[#19c878] transition-all duration-500" style={{ width: `${percentage}%` }} /></div>
    </div>
    <nav aria-label="Onboarding steps" className="flex gap-2 overflow-x-auto p-3 lg:grid lg:gap-1">
      {steps.map(step => {
        const selected = active === step.id;
        return <button type="button" key={step.id} aria-current={selected ? "step" : undefined} onClick={() => onSelect(step.id)} className={`group flex min-w-[180px] items-center gap-3 rounded-xl p-3 text-left transition lg:min-w-0 ${selected ? "bg-[#ecf8f1] ring-1 ring-inset ring-emerald-200" : "hover:bg-[#f5f7f5]"}`}>
          <span className={`grid size-9 shrink-0 place-items-center rounded-xl ${step.complete ? "bg-emerald-600 text-white" : selected ? "bg-white text-emerald-700 shadow-sm" : "bg-[#eef2ef] text-[#7b8a82]"}`}>{step.complete ? <Icon name="check" className="size-4"/> : <Icon name={icons[step.id]} className="size-4"/>}</span>
          <span className="min-w-0"><span className={`block text-xs font-bold ${selected ? "text-[#0b432b]" : "text-[#33443b]"}`}>{step.number}. {step.label}</span><span className="mt-1 block truncate text-[10px] text-[#87968e]">{step.complete ? "Completed" : step.description}</span></span>
        </button>;
      })}
    </nav>
  </aside>;
}
