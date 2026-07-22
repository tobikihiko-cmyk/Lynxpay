"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { Icon } from "@/components/icons";
import { apiErrorMessage } from "@/lib/errors";

type Form = { organization_name: string; full_name: string; contact_email: string; contact_phone: string; password: string };
const journey = ["Create account", "Business profile", "M-PESA setup", "Daraja credentials", "Test payment", "Activation"];

export default function SignUp() {
  const { register, handleSubmit, formState: { isSubmitting } } = useForm<Form>();
  const [error, setError] = useState("");
  const router = useRouter();
  async function submit(values: Form) {
    setError("");
    const response = await fetch("/api/session/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
    const payload = await response.json();
    if (!response.ok) { setError(apiErrorMessage(payload, "Registration failed")); return; }
    router.replace("/verify-email");
    router.refresh();
  }
  return <main className="grid min-h-screen bg-white lg:grid-cols-[.82fr_1.18fr]">
    <section className="relative hidden overflow-hidden bg-[#07150e] p-10 text-white lg:flex lg:flex-col xl:p-14">
      <div className="absolute -right-32 -top-32 size-96 rounded-full bg-emerald-400/[.08] blur-3xl"/><Link href="/" className="relative flex items-center gap-3 text-white no-underline"><span className="grid size-10 place-items-center rounded-[13px] bg-[#20ce7f] text-xs font-black text-[#03140a]">LX</span><strong className="text-lg tracking-[-.03em]">LynxPay</strong></Link>
      <div className="relative my-auto max-w-md"><p className="eyeline !text-emerald-300">Merchant activation</p><h1 className="mt-4 text-5xl font-[740] leading-[1.02] tracking-[-.06em] xl:text-6xl">Your M-PESA infrastructure starts here.</h1><p className="mt-5 max-w-sm text-sm leading-7 text-emerald-50/50">Connect your own Safaricom account. Keep your own settlement path. Gain one auditable control plane for every payment event.</p><ol className="mt-10 grid gap-2">{journey.map((step,index) => <li className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-xs font-semibold ${index === 0 ? "bg-emerald-400/[.12] text-emerald-50 ring-1 ring-inset ring-emerald-300/[.12]" : "text-emerald-50/35"}`} key={step}><span className={`grid size-7 place-items-center rounded-lg text-[10px] font-black ${index === 0 ? "bg-emerald-400 text-[#03140a]" : "bg-white/5"}`}>{index + 1}</span>{step}{index === 0 && <span className="ml-auto text-[8px] uppercase tracking-[.12em] text-emerald-300">Current</span>}</li>)}</ol></div>
      <p className="relative text-[10px] leading-5 text-white/30">Your PayBill or Till · Your Daraja credentials · Your settlement account</p>
    </section>
    <section className="grid place-items-center bg-[radial-gradient(circle_at_90%_0%,rgb(25_200_120/.08),transparent_28rem)] p-5 sm:p-8 xl:p-12">
      <form className="w-full max-w-2xl" onSubmit={handleSubmit(submit)}><div className="flex items-center justify-between"><span className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-[9px] font-black uppercase tracking-[.12em] text-emerald-800">Step 1 of 6</span><Link href="/sign-in" className="text-xs font-bold text-[#087448]">Already have an account?</Link></div><div className="mt-8"><p className="eyeline">Create owner account</p><h2 className="mt-3 text-4xl font-[740] tracking-[-.055em] sm:text-5xl">Connect your business to M-PESA.</h2><p className="mt-4 max-w-xl text-sm leading-6 text-[#607168]">Start with the accountable business owner. We will verify this email before allowing sensitive merchant actions.</p></div>
        <div className="mt-8 grid gap-5 sm:grid-cols-2"><label className="field">Business name<input required minLength={2} autoComplete="organization" placeholder="Acme Limited" {...register("organization_name")}/></label><label className="field">Owner name<input required minLength={2} autoComplete="name" placeholder="Jane Merchant" {...register("full_name")}/></label><label className="field">Work email<input type="email" required autoComplete="email" placeholder="jane@business.co.ke" {...register("contact_email")}/></label><label className="field">Kenyan mobile number<input required inputMode="tel" autoComplete="tel" placeholder="0712 345 678" {...register("contact_phone")}/></label></div><label className="field mt-5">Create password<input type="password" minLength={12} autoComplete="new-password" required {...register("password")}/><span className="font-normal text-[#87968e]">At least 12 characters. Use a unique password that is not shared with Safaricom.</span></label>
        {error && <p role="alert" className="mt-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
        <div className="mt-7 flex flex-wrap items-center justify-between gap-4 border-t border-[#e7ece8] pt-6"><p className="max-w-sm text-[10px] leading-5 text-[#7b8a82]"><Icon name="shield" className="mr-1 inline size-3.5 text-emerald-700"/>Credentials are added later and always encrypted before storage.</p><button className="primary min-w-44" disabled={isSubmitting}>{isSubmitting ? "Creating secure account…" : "Create owner account"}<Icon name="arrow" className="size-4"/></button></div>
      </form>
    </section>
  </main>;
}
