"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { PublicInvoice, invoiceDate, invoiceMoney } from "@/lib/invoices";
import { Icon } from "@/components/icons";
import { StatusPill } from "@/components/status-pill";

type PayResult = {
  invoice: PublicInvoice;
  payment?: {
    id: string;
    status: string;
    checkout_request_id?: string | null;
    provider_acceptance_state?: string;
  } | null;
  already_paid: boolean;
};

export default function PublicInvoicePage() {
  const params = useParams<{ publicId: string }>();
  const publicId = params.publicId;
  const [invoice, setInvoice] = useState<PublicInvoice>();
  const [phone, setPhone] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<PayResult>();
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api<PublicInvoice>(`/public/invoices/${publicId}`).then(result => {
      if (active) setInvoice(result);
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "Invoice could not be loaded");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [publicId]);

  async function pay(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await api<PayResult>(`/public/invoices/${publicId}/pay`, {
        method: "POST",
        body: JSON.stringify({ phone_number: phone })
      });
      setSubmitted(result);
      setInvoice(result.invoice);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not send M-PESA prompt");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <main className="grid min-h-screen place-items-center px-4"><div className="surface w-full max-w-xl p-8"><div className="skeleton h-4 w-32 rounded" /><div className="skeleton mt-6 h-10 w-72 rounded" /><div className="skeleton mt-8 h-44 w-full rounded-xl" /></div></main>;

  if (!invoice) return <main className="grid min-h-screen place-items-center px-4"><section className="surface w-full max-w-xl p-8 text-center"><span className="mx-auto grid size-12 place-items-center rounded-2xl bg-red-50 text-red-700"><Icon name="review" className="size-6" /></span><h1 className="mt-4 text-xl font-bold">Invoice unavailable</h1><p className="mt-2 text-sm leading-6 text-[#607168]">{error || "The invoice link may be incorrect or expired."}</p></section></main>;

  const paid = invoice.status === "paid";
  const unavailable = ["void", "expired"].includes(invoice.status);

  return <main className="min-h-screen bg-[#f4f6f3] px-4 py-8 sm:py-12">
    <section className="mx-auto max-w-3xl">
      <div className="mb-5 flex items-center gap-3">
        <span className="grid size-10 place-items-center rounded-[13px] bg-[#20ce7f] text-[13px] font-black tracking-[-.08em] text-[#03140a]">LX</span>
        <div><p className="text-sm font-[750] tracking-[-.02em]">LynxPay secure invoice</p><p className="text-[11px] font-semibold text-[#7b8a82]">M-PESA collection</p></div>
      </div>

      <article className="surface overflow-hidden">
        <div className="border-b border-[#e4eae6] bg-white p-6 sm:p-8">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <p className="eyeline">Invoice {invoice.invoice_number}</p>
              <h1 className="mt-3 max-w-2xl text-[clamp(2rem,6vw,3.5rem)] font-[740] leading-[1.02] tracking-[-.055em]">{invoice.service_title}</h1>
            </div>
            <StatusPill status={invoice.status} />
          </div>
          <p className="mt-5 max-w-2xl whitespace-pre-wrap text-sm leading-7 text-[#52635a]">{invoice.description}</p>
        </div>

        <div className="grid gap-0 md:grid-cols-[1fr_310px]">
          <div className="p-6 sm:p-8">
            <dl className="grid gap-5 sm:grid-cols-2">
              <div><dt className="text-[10px] font-black uppercase tracking-[.14em] text-[#849189]">Prepared for</dt><dd className="mt-1 text-sm font-bold">{invoice.client_name}</dd></div>
              <div><dt className="text-[10px] font-black uppercase tracking-[.14em] text-[#849189]">Due</dt><dd className="mt-1 text-sm font-bold">{invoiceDate(invoice.due_at)}</dd></div>
              <div><dt className="text-[10px] font-black uppercase tracking-[.14em] text-[#849189]">Merchant</dt><dd className="mt-1 text-sm font-bold">{invoice.merchant.name}</dd></div>
              <div><dt className="text-[10px] font-black uppercase tracking-[.14em] text-[#849189]">M-PESA account</dt><dd className="mt-1 text-sm font-bold capitalize">{invoice.merchant.shortcode_type?.replaceAll("_", " ") || "M-PESA"} {invoice.merchant.till_number || invoice.merchant.shortcode}</dd></div>
            </dl>
            {(invoice.merchant.address || invoice.merchant.email || invoice.merchant.phone) && <div className="mt-7 rounded-xl border border-[#e4eae6] bg-[#fbfcfb] p-4 text-xs leading-6 text-[#607168]">
              {invoice.merchant.address && <p>{invoice.merchant.address}</p>}
              {invoice.merchant.email && <p>{invoice.merchant.email}</p>}
              {invoice.merchant.phone && <p>{invoice.merchant.phone}</p>}
            </div>}
            {invoice.line_items?.length > 0 && <div className="mt-7 overflow-hidden rounded-xl border border-[#e4eae6]">
              <div className="border-b border-[#e4eae6] bg-[#fbfcfb] px-4 py-3 text-[10px] font-black uppercase tracking-[.14em] text-[#849189]">Items</div>
              <div className="divide-y divide-[#edf1ee]">{invoice.line_items.map(item => <div key={item.id} className="grid grid-cols-[1fr_auto] gap-4 px-4 py-3 text-sm">
                <div><p className="font-bold">{item.name}</p><p className="mt-1 text-[11px] capitalize text-[#849189]">{item.item_type} · {Number(item.quantity).toLocaleString("en-KE")} × KES {Number(item.unit_price).toLocaleString("en-KE")}</p>{item.description && <p className="mt-1 text-xs leading-5 text-[#607168]">{item.description}</p>}</div>
                <strong className="whitespace-nowrap tabular-nums">KES {Number(item.line_total).toLocaleString("en-KE")}</strong>
              </div>)}</div>
            </div>}
          </div>

          <aside className="border-t border-[#e4eae6] bg-[#fbfcfb] p-6 sm:p-8 md:border-l md:border-t-0">
            <p className="text-[10px] font-black uppercase tracking-[.14em] text-[#849189]">Amount due</p>
            <strong className="mt-2 block text-4xl font-[760] tracking-[-.055em]">{invoiceMoney(invoice)}</strong>
            {paid ? <div className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm font-semibold text-emerald-800"><Icon name="check" className="mb-2 size-5" />This invoice is paid.</div> : unavailable ? <div className="mt-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm font-semibold text-amber-900">This invoice is no longer payable.</div> : submitted ? <div className="mt-6 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm leading-6 text-emerald-900"><strong className="block">M-PESA prompt sent</strong>Check your phone and enter your M-PESA PIN. This invoice updates after Safaricom confirms payment.</div> : <form onSubmit={pay} className="mt-6 grid gap-4">
              <label className="field">M-PESA phone number
                <input inputMode="tel" value={phone} onChange={event => setPhone(event.target.value)} placeholder="0712 345 678" required />
              </label>
              {error && <p role="alert" className="rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-800">{error}</p>}
              <button className="primary w-full" type="submit" disabled={submitting}><Icon name="payments" className="size-4" />{submitting ? "Sending prompt…" : "Send M-PESA prompt"}</button>
            </form>}
          </aside>
        </div>
      </article>
    </section>
  </main>;
}
