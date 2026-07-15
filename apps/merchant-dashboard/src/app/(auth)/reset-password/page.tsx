"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

function ResetForm() {
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setSubmitting(true);
    const response = await fetch("/api/lynxpay/auth/password-reset/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token, new_password: password }) });
    setMessage(response.ok ? "Password reset. Sign in with your new password." : "The reset link is invalid or expired."); setSubmitting(false);
  }
  return <form onSubmit={submit} className="surface grid w-full max-w-md gap-5 p-8"><div><p className="eyeline">Secure recovery</p><h1 className="mt-2 text-3xl font-bold tracking-[-.04em]">Choose a new password</h1><p className="mt-2 text-sm leading-6 text-[#607168]">Use at least 12 characters and avoid a password used by another service.</p></div><label className="field">New password<input type="password" minLength={12} autoComplete="new-password" value={password} onChange={event => setPassword(event.target.value)} required /></label><button className="primary" disabled={!token || submitting}>{submitting ? "Securing account…" : "Reset password"}</button>{!token && <p role="alert" className="text-sm text-red-700">This reset link is missing its secure token.</p>}{message && <div><p role="status" className="text-sm text-[#415048]">{message}</p><Link href="/sign-in" className="mt-3 inline-block text-xs font-bold text-[#087448]">Return to sign in</Link></div>}</form>;
}

export default function ResetPasswordPage() { return <main className="grid min-h-screen place-items-center p-6"><Suspense fallback={<div className="skeleton h-96 w-full max-w-md rounded-2xl"/>}><ResetForm /></Suspense></main>; }
