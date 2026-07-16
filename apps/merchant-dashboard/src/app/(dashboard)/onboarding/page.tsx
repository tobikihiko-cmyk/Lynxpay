"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { AccountStep, ActivationStep, BusinessStep, CredentialsStep, MpesaStep, VerificationStep } from "@/components/onboarding-steps";
import { OnboardingProgress } from "@/components/onboarding-progress";
import { Icon } from "@/components/icons";
import { PageHeader } from "@/components/ui";
import { deriveOnboarding, type CurrentUser, type Merchant, type OnboardingSnapshot, type OnboardingStepId, type Organization } from "@/lib/onboarding";
import type { Payment } from "@/components/payment-table";

type OnboardingData = OnboardingSnapshot & { merchants: Merchant[] };

async function requestOnboardingData(preferredMerchantId?: string): Promise<OnboardingData> {
  const [user, organization, merchantPage] = await Promise.all([
    api<CurrentUser>("/auth/me"),
    api<Organization>("/organization"),
    api<{ items: Merchant[] }>("/merchants?limit=100")
  ]);
  const merchant = merchantPage.items.find(item => item.id === preferredMerchantId) || merchantPage.items[0];
  let verificationPayment: Payment | undefined;
  if (merchant) {
    const paymentPage = await api<{ items: Payment[] }>(`/payments?merchant_id=${encodeURIComponent(merchant.id)}&purpose=merchant_verification&limit=1`);
    verificationPayment = paymentPage.items[0];
  }
  return { user, organization, merchants: merchantPage.items, merchant, verificationPayment };
}

export default function OnboardingPage() {
  const [data, setData] = useState<OnboardingData>();
  const [active, setActive] = useState<OnboardingStepId>("account");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    requestOnboardingData().then(result => {
      if (!mounted) return;
      setData(result);
      setActive(deriveOnboarding(result).next);
    }).catch(caught => { if (mounted) setError(caught instanceof Error ? caught.message : "Could not load onboarding"); }).finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, []);

  const refresh = useCallback(async (merchantId?: string, nextStep?: OnboardingStepId) => {
    setError("");
    try {
      const result = await requestOnboardingData(merchantId);
      setData(result);
      if (nextStep) setActive(nextStep);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not refresh onboarding"); }
  }, []);

  const progress = useMemo(() => data ? deriveOnboarding(data) : undefined, [data]);

  if (loading) return <section aria-label="Loading onboarding"><div className="skeleton h-4 w-32 rounded"/><div className="skeleton mt-4 h-14 max-w-2xl rounded-xl"/><div className="mt-8 grid gap-5 lg:grid-cols-[300px_minmax(0,1fr)]"><div className="skeleton h-[560px] rounded-2xl"/><div className="skeleton h-[620px] rounded-2xl"/></div></section>;
  if (!data || !progress) return <section><PageHeader eyebrow="Daraja activation" title="Onboarding unavailable" description={error || "The merchant setup state could not be loaded."}/><button className="primary mt-6" onClick={() => window.location.reload()}>Try again</button></section>;

  const shared = { user: data.user, organization: data.organization, merchant: data.merchant, onRefresh: refresh, onNavigate: setActive };
  const content = {
    account: <AccountStep {...shared}/>,
    business: <BusinessStep {...shared}/>,
    mpesa: <MpesaStep {...shared}/>,
    credentials: <CredentialsStep {...shared}/>,
    verification: <VerificationStep {...shared} verificationPayment={data.verificationPayment}/>,
    activation: <ActivationStep {...shared} verificationPayment={data.verificationPayment}/>
  }[active];

  return <section>
    <PageHeader eyebrow="Daraja activation" title="Launch your M-PESA integration" description="A controlled six-step path from verified ownership to callback-proven payment infrastructure. Your PayBill, credentials, and settlement account remain yours." action={data.merchants.length > 1 ? <label className="field min-w-52">Merchant<select value={data.merchant?.id} onChange={event => { const merchantId = event.target.value; void requestOnboardingData(merchantId).then(result => { setData(result); setActive(deriveOnboarding(result).next); }); }}>{data.merchants.map(merchant => <option value={merchant.id} key={merchant.id}>{merchant.merchant_name} · {merchant.environment}</option>)}</select></label> : <span className="inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-3 py-2 text-[10px] font-black uppercase tracking-[.1em] text-emerald-800"><Icon name="shield" className="size-3.5"/>Tenant isolated</span>} />
    {error && <p role="alert" className="mt-5 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</p>}
    <div className="mt-8 grid gap-5 lg:grid-cols-[300px_minmax(0,1fr)] xl:gap-7">
      <OnboardingProgress steps={progress.steps} active={active} percentage={progress.percentage} onSelect={setActive}/>
      <div className="min-w-0 rounded-[20px] border border-[#dfe6e1] bg-white p-5 shadow-[var(--shadow-sm)] sm:p-7 xl:p-9">{content}</div>
    </div>
  </section>;
}
