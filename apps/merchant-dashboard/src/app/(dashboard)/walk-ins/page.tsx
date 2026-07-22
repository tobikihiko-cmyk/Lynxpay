"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { CatalogItem } from "@/lib/invoices";
import { Payment, PaymentTable } from "@/components/payment-table";
import { Icon } from "@/components/icons";
import { EmptyState, LoadingTable, MetricCard, PageHeader } from "@/components/ui";

type Merchant = {
  id: string;
  merchant_name: string;
  shortcode: string;
  shortcode_type: string;
  status: string;
};
type MerchantPage = { items: Merchant[] };
type CatalogPage = { items: CatalogItem[] };
type PaymentPage = { items: Payment[] };
type SaleLine = { catalog_item_id: string; quantity: string };
type StkResult = Payment & { attempt?: { status: string; checkout_request_id?: string | null; response_description?: string | null } };

function lineTotal(item: CatalogItem, quantity: string) {
  return Number(item.unit_price) * Number(quantity || "1");
}

function makeReference() {
  const stamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
  const suffix = Math.random().toString(36).slice(2, 8).toUpperCase();
  return `WALKIN-${stamp}-${suffix}`;
}

export default function WalkInsPage() {
  const [merchants, setMerchants] = useState<Merchant[]>([]);
  const [merchantId, setMerchantId] = useState("");
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [lines, setLines] = useState<SaleLine[]>([]);
  const [recent, setRecent] = useState<Payment[]>([]);
  const [phone, setPhone] = useState("");
  const [description, setDescription] = useState("");
  const [manualAmount, setManualAmount] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<StkResult>();

  const merchantCatalog = useMemo(() => catalog.filter(item => item.merchant_id === merchantId), [catalog, merchantId]);
  const selected = useMemo(() => lines.map(line => ({ line, item: catalog.find(item => item.id === line.catalog_item_id) })).filter((row): row is { line: SaleLine; item: CatalogItem } => Boolean(row.item)), [catalog, lines]);
  const selectedTotal = useMemo(() => selected.reduce((total, row) => total + lineTotal(row.item, row.line.quantity), 0), [selected]);
  const total = selected.length ? selectedTotal : Number(manualAmount || "0");
  const activeMerchant = merchants.find(merchant => merchant.id === merchantId);

  useEffect(() => {
    let active = true;
    Promise.all([
      api<MerchantPage>("/merchants?limit=100"),
      api<CatalogPage>("/catalog-items?status=active"),
      api<PaymentPage>("/payments?search=WALKIN-&limit=20")
    ]).then(([merchantResult, catalogResult, paymentResult]) => {
      if (!active) return;
      const defaultMerchant = merchantResult.items.find(item => item.status === "active") || merchantResult.items[0];
      setMerchants(merchantResult.items);
      setMerchantId(defaultMerchant?.id || "");
      setCatalog(catalogResult.items);
      setRecent(paymentResult.items);
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "Could not load walk-in screen");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  function addLine(item: CatalogItem) {
    setLines(current => current.some(line => line.catalog_item_id === item.id) ? current : [...current, { catalog_item_id: item.id, quantity: "1" }]);
    setDescription(current => current || item.name);
    setManualAmount("");
  }

  function clearSale() {
    setLines([]);
    setPhone("");
    setDescription("");
    setManualAmount("");
    setResult(undefined);
    setError("");
  }

  async function sendPrompt(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSending(true);
    setError("");
    setResult(undefined);
    try {
      const external_reference = makeReference();
      const saleDescription = selected.length
        ? selected.map(({ item, line }) => `${Number(line.quantity).toLocaleString("en-KE")} x ${item.name}`).join(", ")
        : description.trim();
      const payload = {
        merchant_id: merchantId,
        amount: String(total),
        phone_number: phone.trim(),
        external_reference,
        description: saleDescription.slice(0, 300),
        customer_name: "Walk-in customer",
        callback_metadata: {
          sale_channel: "walk_in",
          source: "merchant_dashboard",
          items: selected.map(({ item, line }) => ({
            catalog_item_id: item.id,
            item_type: item.item_type,
            name: item.name,
            quantity: line.quantity,
            unit_price: item.unit_price,
            line_total: String(lineTotal(item, line.quantity))
          }))
        }
      };
      const payment = await api<StkResult>("/payments/stk-push", {
        method: "POST",
        headers: { "Idempotency-Key": external_reference },
        body: JSON.stringify(payload)
      });
      setResult(payment);
      setRecent(current => [payment, ...current.filter(item => item.id !== payment.id)].slice(0, 20));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not send M-PESA prompt");
    } finally {
      setSending(false);
    }
  }

  return <section>
    <PageHeader eyebrow="Counter collection" title="Walk-ins" description="For customers who are physically present: select the service or enter a custom amount, type their M-PESA number, send STK, and let the payment ledger confirm it." action={<Link className="secondary no-underline" href="/catalog"><Icon name="invoices" className="size-4" />Manage catalog</Link>} />

    <div className="my-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Sale amount" value={`KES ${Number(total || 0).toLocaleString("en-KE")}`} detail={selected.length ? "Calculated from selected items" : "Manual counter amount"} icon="payments" />
      <MetricCard label="Selected items" value={String(selected.length)} detail="Saved services or products on this ticket" icon="invoices" />
      <MetricCard label="Merchant" value={activeMerchant?.merchant_name || "None"} detail={activeMerchant ? `${activeMerchant.shortcode_type} ${activeMerchant.shortcode}` : "Select an active merchant"} icon="check" />
      <MetricCard label="Recent walk-ins" value={String(recent.length)} detail="Loaded from payment references" icon="clock" />
    </div>

    <form onSubmit={sendPrompt} className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
      <section className="surface p-5 md:p-6">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="field">Merchant
            <select value={merchantId} onChange={event => { setMerchantId(event.target.value); setLines([]); setResult(undefined); }} required>
              <option value="">Select merchant</option>
              {merchants.map(merchant => <option key={merchant.id} value={merchant.id}>{merchant.merchant_name} · {merchant.shortcode_type} {merchant.shortcode}</option>)}
            </select>
          </label>
          <label className="field">Customer M-PESA number
            <input inputMode="tel" value={phone} onChange={event => setPhone(event.target.value)} placeholder="0712 345 678" required />
          </label>
        </div>

        <div className="mt-5 rounded-xl border border-[#dfe6e1] bg-[#fbfcfb] p-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div><p className="text-xs font-bold text-[#415048]">Saved catalog items</p><p className="mt-1 text-[11px] text-[#7b8a82]">Only this merchant&apos;s active catalog appears here.</p></div>
            <Link href="/catalog" className="quiet-button min-h-9 px-3 text-xs no-underline"><Icon name="arrow" className="size-4" />Edit catalog</Link>
          </div>
          {merchantCatalog.length ? <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {merchantCatalog.map(item => <button key={item.id} type="button" className="rounded-xl border border-[#e4eae6] bg-white p-3 text-left transition hover:border-[#aebdb4] hover:bg-[#fbfcfb]" onClick={() => addLine(item)}>
              <span className="block truncate text-sm font-bold">{item.name}</span>
              <span className="mt-1 block text-[11px] capitalize text-[#7b8a82]">{item.item_type} · KES {Number(item.unit_price).toLocaleString("en-KE")}</span>
            </button>)}
          </div> : <div className="mt-4 rounded-xl border border-dashed border-[#cbd6cf] bg-white p-4 text-sm text-[#607168]">No saved items for this merchant. Use manual amount, or add services/products in Catalog.</div>}
        </div>

        {selected.length > 0 && <div className="mt-5 grid gap-2">
          {selected.map(({ item, line }) => <div key={item.id} className="grid grid-cols-[1fr_86px_100px_34px] items-center gap-2 rounded-xl border border-[#e4eae6] bg-white p-3 text-sm">
            <div className="min-w-0"><p className="truncate font-bold">{item.name}</p><p className="text-[11px] text-[#849189]">KES {Number(item.unit_price).toLocaleString("en-KE")}</p></div>
            <input aria-label={`Quantity for ${item.name}`} className="control min-h-9 px-2 text-sm" inputMode="decimal" value={line.quantity} onChange={event => setLines(current => current.map(row => row.catalog_item_id === item.id ? { ...row, quantity: event.target.value } : row))} />
            <strong className="text-right tabular-nums">KES {lineTotal(item, line.quantity).toLocaleString("en-KE")}</strong>
            <button type="button" className="quiet-button grid size-8 place-items-center p-0" onClick={() => setLines(current => current.filter(row => row.catalog_item_id !== item.id))} aria-label={`Remove ${item.name}`}><Icon name="close" className="size-4" /></button>
          </div>)}
        </div>}

        <div className="mt-5 grid gap-4 md:grid-cols-[160px_1fr]">
          <label className="field">Manual amount
            <input inputMode="numeric" value={selected.length ? String(selectedTotal) : manualAmount} onChange={event => setManualAmount(event.target.value)} placeholder="500" disabled={selected.length > 0} required={!selected.length} />
          </label>
          <label className="field">Sale description
            <input value={description} onChange={event => setDescription(event.target.value)} placeholder="Adult haircut" required={!selected.length} />
          </label>
        </div>
      </section>

      <aside className="surface h-fit p-5 md:p-6">
        <p className="eyeline">Ready to prompt</p>
        <strong className="mt-3 block text-4xl font-[760] tracking-[-.055em]">KES {Number(total || 0).toLocaleString("en-KE")}</strong>
        <p className="mt-3 text-sm leading-6 text-[#607168]">{selected.length ? selected.map(({ item, line }) => `${line.quantity} x ${item.name}`).join(", ") : description || "Manual walk-in sale"}</p>
        {error && <p role="alert" className="mt-4 rounded-xl border border-red-200 bg-red-50 p-3 text-xs leading-5 text-red-800">{error}</p>}
        {result && <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm leading-6 text-emerald-900"><strong className="block">Prompt created</strong>{result.status.replaceAll("_", " ")} · {result.checkout_request_id || result.attempt?.checkout_request_id || "Awaiting checkout ID"}</div>}
        <div className="mt-5 flex flex-col gap-2">
          <button className="primary w-full" type="submit" disabled={sending || !merchantId || !phone || total <= 0}><Icon name="payments" className="size-4" />{sending ? "Sending prompt..." : "Send M-PESA prompt"}</button>
          <button className="secondary w-full" type="button" onClick={clearSale}>New walk-in sale</button>
        </div>
      </aside>
    </form>

    <section className="mt-7">
      <PageHeader eyebrow="Evidence" title="Recent walk-in payments" description="These are ordinary LynxPay payments with walk-in references. Callback or status-query evidence decides the final state." />
      <div className="mt-5">{loading ? <LoadingTable /> : recent.length ? <PaymentTable payments={recent} /> : <EmptyState icon="payments" title="No walk-in prompts yet" description="Send a prompt from this screen and it will appear here with the normal payment evidence." />}</div>
    </section>
  </section>;
}
