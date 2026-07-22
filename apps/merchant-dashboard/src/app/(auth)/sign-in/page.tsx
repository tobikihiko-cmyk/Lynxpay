"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { apiErrorMessage } from "@/lib/errors";

type Form = { email: string; password: string; mfa_code?: string };
export default function SignIn() {
  const { register, handleSubmit, formState: { isSubmitting } } = useForm<Form>(); const [error, setError] = useState(""); const router = useRouter();
  async function submit(values: Form) { setError(""); const body = { email: values.email, password: values.password, ...(values.mfa_code?.trim() ? { mfa_code: values.mfa_code.trim() } : {}) }; const response = await fetch("/api/session/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); const payload = await response.json(); if (!response.ok) return setError(apiErrorMessage(payload, "Sign in failed")); router.replace("/payments"); router.refresh(); }
  return <main className="grid min-h-screen lg:grid-cols-[1.15fr_.85fr]"><section className="hidden bg-[#06130c] p-16 text-white lg:flex lg:flex-col lg:justify-between"><strong className="text-xl">LynxPay</strong><div className="max-w-2xl"><p className="eyeline !text-emerald-300">BUILT FOR SAFARICOM DARAJA</p><h1 className="mt-5 text-7xl font-bold leading-[.94] tracking-[-.07em]">Payment certainty for M-PESA.</h1><p className="mt-7 max-w-xl text-lg leading-8 text-emerald-50/55">One operational truth for every STK request, callback, receipt, retry and reconciliation event.</p></div><small className="text-white/35">Your PayBill. Your credentials. Your settlement account.</small></section><section className="grid place-items-center p-6"><form onSubmit={handleSubmit(submit)} className="surface grid w-full max-w-md gap-5 p-8"><div><p className="eyeline">MERCHANT ACCESS</p><h2 className="mt-2 text-3xl font-bold tracking-tight">Welcome back</h2><p className="mt-2 text-sm text-slate-500">Enter the M-PESA operations workspace.</p></div><label className="field">Work email<input type="email" autoComplete="username" required {...register("email")} /></label><label className="field">Password<input type="password" autoComplete="current-password" required {...register("password")} /></label><label className="field">MFA or recovery code<input autoComplete="one-time-code" {...register("mfa_code")} /></label>{error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}<button className="primary" disabled={isSubmitting}>{isSubmitting ? "Authenticating…" : "Enter LynxPay"}</button></form></section></main>;
}
