"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { Icon } from "@/components/icons";

function VerificationStatus() {
  const token = useSearchParams().get("token");
  const [message, setMessage] = useState(token ? "Verifying your email…" : "Open the verification link sent to your work email.");
  const [verified, setVerified] = useState(false);
  useEffect(() => { if (!token) return; let active = true; fetch("/api/lynxpay/auth/email-verification/confirm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token }) }).then(response => { if (!active) return; setVerified(response.ok); setMessage(response.ok ? "Email verified. Your owner account is protected." : "This verification link is invalid or expired."); }); return () => { active = false; }; }, [token]);
  return <section className="surface w-full max-w-md p-8"><span className={`grid size-12 place-items-center rounded-2xl ${verified ? "bg-emerald-50 text-emerald-700" : "bg-[#eef2ef] text-[#607168]"}`}><Icon name={verified ? "check" : "shield"} className="size-6"/></span><p className="eyeline mt-6">Email verification</p><h1 className="mt-2 text-3xl font-bold tracking-[-.04em]">Protect the merchant owner account</h1><p role="status" className="mt-5 text-sm leading-6 text-[#607168]">{message}</p><Link className="primary mt-6 no-underline" href={verified ? "/onboarding" : "/sign-in"}>{verified ? "Continue onboarding" : "Return to sign in"}</Link></section>;
}

export default function VerifyEmailPage() { return <main className="grid min-h-screen place-items-center p-6"><Suspense fallback={<div className="skeleton h-96 w-full max-w-md rounded-2xl"/>}><VerificationStatus /></Suspense></main>; }
