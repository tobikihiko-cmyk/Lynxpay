"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { absoluteInvoiceLink, CatalogItem, Invoice, invoiceDate, invoiceMoney } from "@/lib/invoices";
import { Icon } from "@/components/icons";
import { EmptyState, LoadingTable, MetricCard, PageHeader } from "@/components/ui";
import { StatusPill } from "@/components/status-pill";

type Merchant = {
  id: string;
  merchant_name: string;
  shortcode: string;
  shortcode_type: string;
  status: string;
};
type MerchantPage = { items: Merchant[] };
type InvoicePage = { items: Invoice[]; next_before?: string | null };
type CatalogPage = { items: CatalogItem[]; limit: number };
type DraftLine = { catalog_item_id: string; quantity: string };

const statuses = ["sent", "paid", "void", "expired"];

function copyText(value: string) {
  if (navigator.clipboard) return navigator.clipboard.writeText(value);
  return Promise.reject(new Error("Clipboard is not available"));
}

function lineTotal(item: CatalogItem, quantity: string) {
  return Number(item.unit_price) * Number(quantity || "1");
}

export default function InvoicesPage() {
  const [merchants, setMerchants] = useState<Merchant[]>([]);
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [items, setItems] = useState<Invoice[]>([]);
  const [draftLines, setDraftLines] = useState<DraftLine[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [copied, setCopied] = useState("");
  const [error, setError] = useState("");
  const [form, setForm] = useState({
    merchant_id: "",
    invoice_number: "",
    client_name: "",
    client_phone: "",
    client_email: "",
    service_title: "",
    description: "",
    amount: "",
    due_at: "",
    memo: ""
  });

  const activeMerchantCatalog = useMemo(() => catalog.filter(item => item.merchant_id === form.merchant_id), [catalog, form.merchant_id]);
  const selectedItems = useMemo(() => draftLines.map(line => ({ line, item: catalog.find(item => item.id === line.catalog_item_id) })).filter((row): row is { line: DraftLine; item: CatalogItem } => Boolean(row.item)), [catalog, draftLines]);
  const selectedTotal = useMemo(() => selectedItems.reduce((total, row) => total + lineTotal(row.item, row.line.quantity), 0), [selectedItems]);

  const load = useCallback(async ({ before, append = false }: { before?: string; append?: boolean } = {}) => {
    setError("");
    if (!append) setLoading(true);
    try {
      const query = new URLSearchParams({ limit: "50" });
      if (search.trim()) query.set("search", search.trim());
      if (status) query.set("status", status);
      if (before) query.set("before", before);
      const [invoiceResult, merchantResult, catalogResult] = await Promise.all([
        api<InvoicePage>(`/invoices?${query}`),
        api<MerchantPage>("/merchants?limit=100"),
        api<CatalogPage>("/catalog-items?status=active")
      ]);
      setItems(current => append ? [...current, ...invoiceResult.items] : invoiceResult.items);
      setNextBefore(invoiceResult.next_before || null);
      setMerchants(merchantResult.items);
      setCatalog(catalogResult.items);
      setForm(current => current.merchant_id ? current : { ...current, merchant_id: merchantResult.items.find(item => item.status === "active")?.id || merchantResult.items[0]?.id || "" });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load invoices");
    } finally {
      setLoading(false);
    }
  }, [search, status]);

  useEffect(() => {
    let active = true;
    Promise.all([
      api<InvoicePage>("/invoices?limit=50"),
      api<MerchantPage>("/merchants?limit=100"),
      api<CatalogPage>("/catalog-items?status=active")
    ]).then(([invoiceResult, merchantResult, catalogResult]) => {
      if (!active) return;
      setItems(invoiceResult.items);
      setNextBefore(invoiceResult.next_before || null);
      setMerchants(merchantResult.items);
      setCatalog(catalogResult.items);
      setForm(current => current.merchant_id ? current : { ...current, merchant_id: merchantResult.items.find(item => item.status === "active")?.id || merchantResult.items[0]?.id || "" });
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "Could not load invoices");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const summary = useMemo(() => ({
    outstanding: items.filter(item => item.status === "sent").reduce((total, item) => total + Number(item.amount), 0),
    paid: items.filter(item => item.status === "paid").reduce((total, item) => total + Number(item.amount), 0),
    openCount: items.filter(item => item.status === "sent").length,
    paidCount: items.filter(item => item.status === "paid").length
  }), [items]);

  function addLine(item: CatalogItem) {
    setDraftLines(current => current.some(line => line.catalog_item_id === item.id) ? current : [...current, { catalog_item_id: item.id, quantity: "1" }]);
    setForm(current => ({
      ...current,
      service_title: current.service_title || item.name,
      description: current.description || item.description || item.name,
      amount: ""
    }));
  }

  async function createInvoice(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const hasLines = selectedItems.length > 0;
      const payload = {
        merchant_id: form.merchant_id,
        ...(form.invoice_number.trim() ? { invoice_number: form.invoice_number.trim() } : {}),
        client_name: form.client_name.trim(),
        ...(form.client_phone.trim() ? { client_phone: form.client_phone.trim() } : {}),
        ...(form.client_email.trim() ? { client_email: form.client_email.trim() } : {}),
        service_title: form.service_title.trim(),
        description: form.description.trim(),
        ...(hasLines ? { line_items: selectedItems.map(({ line }) => ({ catalog_item_id: line.catalog_item_id, quantity: line.quantity })) } : { amount: form.amount }),
        ...(form.due_at ? { due_at: new Date(`${form.due_at}T12:00:00+03:00`).toISOString() } : {}),
        ...(form.memo.trim() ? { memo: form.memo.trim() } : {})
      };
      const invoice = await api<Invoice>("/invoices", { method: "POST", body: JSON.stringify(payload) });
      setItems(current => [invoice, ...current]);
      setDraftLines([]);
      setForm(current => ({ ...current, invoice_number: "", client_name: "", client_phone: "", client_email: "", service_title: "", description: "", amount: "", due_at: "", memo: "" }));
      const link = absoluteInvoiceLink(invoice.payment_link);
      await copyText(link).catch(() => undefined);
      setCopied(invoice.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create invoice");
    } finally {
      setSaving(false);
    }
  }

  async function copyInvoice(invoice: Invoice) {
    const link = absoluteInvoiceLink(invoice.payment_link);
    await copyText(link);
    setCopied(invoice.id);
  }

  return <section>
    <PageHeader eyebrow="Collect with context" title="Invoices" description="Create a payment link from a freeform service description or from this merchant's saved catalog items." action={<Link className="secondary no-underline" href="/catalog"><Icon name="invoices" className="size-4" />Edit catalog</Link>} />

    <div className="my-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Open invoices" value={String(summary.openCount)} detail="Waiting for client payment" icon="invoices" />
      <MetricCard label="Outstanding" value={`KES ${summary.outstanding.toLocaleString("en-KE")}`} detail="Open invoice value in this view" icon="clock" tone="warning" />
      <MetricCard label="Paid invoices" value={String(summary.paidCount)} detail="Confirmed by payment evidence" icon="check" tone="positive" />
      <MetricCard label="Collected" value={`KES ${summary.paid.toLocaleString("en-KE")}`} detail="Paid invoice value in this view" icon="payments" tone="positive" />
    </div>

    <form onSubmit={createInvoice} className="surface grid gap-5 p-5 md:p-6">
      <div className="grid gap-4 md:grid-cols-3">
        <label className="field">Merchant
          <select value={form.merchant_id} onChange={event => { setForm({ ...form, merchant_id: event.target.value }); setDraftLines([]); }} required>
            <option value="">Select merchant</option>
            {merchants.map(merchant => <option key={merchant.id} value={merchant.id}>{merchant.merchant_name} · {merchant.shortcode_type} {merchant.shortcode}</option>)}
          </select>
        </label>
        <label className="field">Invoice number
          <input value={form.invoice_number} onChange={event => setForm({ ...form, invoice_number: event.target.value })} placeholder="Auto-generated if blank" />
        </label>
        <label className="field">Amount
          <input inputMode="numeric" value={selectedItems.length ? String(selectedTotal) : form.amount} onChange={event => setForm({ ...form, amount: event.target.value })} placeholder="5000" required={!selectedItems.length} readOnly={Boolean(selectedItems.length)} />
        </label>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <label className="field">Client name
          <input value={form.client_name} onChange={event => setForm({ ...form, client_name: event.target.value })} placeholder="Jane Wanjiku" required />
        </label>
        <label className="field">Client phone
          <input value={form.client_phone} onChange={event => setForm({ ...form, client_phone: event.target.value })} placeholder="0712 345 678" />
        </label>
        <label className="field">Client email
          <input type="email" value={form.client_email} onChange={event => setForm({ ...form, client_email: event.target.value })} placeholder="client@example.com" />
        </label>
      </div>

      <section className="rounded-xl border border-[#dfe6e1] bg-[#fbfcfb] p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><p className="text-xs font-bold text-[#415048]">Saved items for this merchant</p><p className="mt-1 text-[11px] text-[#7b8a82]">Optional. Add saved services or products, or leave this empty and type the invoice manually.</p></div>
          <Link href="/catalog" className="quiet-button min-h-9 px-3 text-xs no-underline"><Icon name="arrow" className="size-4" />Manage catalog</Link>
        </div>
        {activeMerchantCatalog.length ? <div className="mt-4 flex flex-wrap gap-2">
          {activeMerchantCatalog.map(item => <button key={item.id} type="button" className="secondary min-h-9 px-3 text-xs" onClick={() => addLine(item)}>{item.name} · KES {Number(item.unit_price).toLocaleString("en-KE")}</button>)}
        </div> : <div className="mt-4 rounded-xl border border-dashed border-[#cbd6cf] bg-white p-4 text-sm text-[#607168]">No saved items for this merchant yet. Create them on the Catalog tab, or continue with a manual invoice below.</div>}

        {selectedItems.length > 0 && <div className="mt-4 grid gap-2">
          {selectedItems.map(({ item, line }) => <div key={item.id} className="grid grid-cols-[1fr_86px_96px_34px] items-center gap-2 rounded-lg bg-white p-2 text-sm">
            <div className="min-w-0"><p className="truncate font-bold">{item.name}</p><p className="text-[11px] text-[#849189]">KES {Number(item.unit_price).toLocaleString("en-KE")}</p></div>
            <input aria-label={`Quantity for ${item.name}`} className="control min-h-9 px-2 text-sm" inputMode="decimal" value={line.quantity} onChange={event => setDraftLines(current => current.map(row => row.catalog_item_id === item.id ? { ...row, quantity: event.target.value } : row))} />
            <strong className="text-right tabular-nums">KES {lineTotal(item, line.quantity).toLocaleString("en-KE")}</strong>
            <button type="button" className="quiet-button grid size-8 place-items-center p-0" onClick={() => setDraftLines(current => current.filter(row => row.catalog_item_id !== item.id))} aria-label={`Remove ${item.name}`}><Icon name="close" className="size-4" /></button>
          </div>)}
          <div className="flex justify-end border-t border-[#e4eae6] pt-3"><strong className="text-sm">Total KES {selectedTotal.toLocaleString("en-KE")}</strong></div>
        </div>}
      </section>

      <div className="grid gap-4 md:grid-cols-[1fr_220px]">
        <label className="field">Service being paid for
          <input value={form.service_title} onChange={event => setForm({ ...form, service_title: event.target.value })} placeholder="Legal consultation and filing" required />
        </label>
        <label className="field">Due date
          <input type="date" value={form.due_at} onChange={event => setForm({ ...form, due_at: event.target.value })} />
        </label>
      </div>
      <label className="field">Invoice description
        <textarea value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} placeholder="Prepared services, case reference, dates, or invoice notes the client should see." required />
      </label>
      <label className="field">Internal memo
        <textarea value={form.memo} onChange={event => setForm({ ...form, memo: event.target.value })} placeholder="Optional note for your team" />
      </label>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button type="button" className="quiet-button" onClick={() => setDraftLines([])} disabled={!draftLines.length}>Clear selected items</button>
        <button className="primary" type="submit" disabled={saving || !form.merchant_id}><Icon name="invoices" className="size-4" />{saving ? "Creating…" : "Create invoice"}</button>
      </div>
    </form>

    <div className="mt-6 rounded-2xl border border-[#dfe6e1] bg-white p-3 shadow-[0_1px_2px_rgb(7_21_14/.03)]">
      <form onSubmit={event => { event.preventDefault(); void load(); }} className="flex flex-wrap items-center gap-2">
        <label className="relative min-w-[240px] flex-1"><span className="sr-only">Search invoices</span><Icon name="search" className="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-[#8a9891]"/><input aria-label="Search invoices" className="control pl-10 text-sm" placeholder="Invoice, client, phone or service" value={search} onChange={event => setSearch(event.target.value)} /></label>
        <select aria-label="Invoice status" className="control min-w-36 text-sm font-semibold" value={status} onChange={event => setStatus(event.target.value)}><option value="">All invoices</option>{statuses.map(value => <option value={value} key={value}>{value}</option>)}</select>
        <button className="primary" type="submit" disabled={loading}>Apply filters</button>
      </form>
    </div>

    {error && <div role="alert" className="my-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
    {loading ? <LoadingTable /> : items.length ? <div className="surface mt-4 overflow-hidden"><div className="overflow-x-auto"><table className="w-full min-w-[980px] border-collapse text-left text-sm">
      <thead><tr className="border-b border-[#e4eae6] bg-[#fafbfa] text-[9px] font-black uppercase tracking-[.14em] text-[#849189]"><th className="px-5 py-3.5">Invoice</th><th className="px-4 py-3.5">Client</th><th className="px-4 py-3.5">State</th><th className="px-4 py-3.5 text-right">Amount</th><th className="px-4 py-3.5">Due</th><th className="px-4 py-3.5">Link</th></tr></thead>
      <tbody>{items.map(invoice => {
        const link = absoluteInvoiceLink(invoice.payment_link);
        const smsLink = invoice.client_phone ? `sms:${invoice.client_phone}?&body=${encodeURIComponent(`Please pay invoice ${invoice.invoice_number}: ${link}`)}` : "";
        return <tr key={invoice.id} className="border-b border-[#edf1ee] bg-white last:border-0 hover:bg-[#f9fbf9]">
          <td className="px-5 py-4"><strong className="block tracking-[-.01em]">{invoice.invoice_number}</strong><span className="mt-1 block max-w-md truncate text-[11px] text-[#849189]">{invoice.service_title}{invoice.line_items?.length ? ` · ${invoice.line_items.length} item${invoice.line_items.length === 1 ? "" : "s"}` : ""}</span></td>
          <td className="px-4 py-4"><span className="block font-semibold">{invoice.client_name}</span><span className="mt-1 block text-[11px] text-[#849189]">{invoice.client_phone || invoice.client_email || "Link recipient chooses phone"}</span></td>
          <td className="px-4 py-4"><StatusPill status={invoice.status} /></td>
          <td className="whitespace-nowrap px-4 py-4 text-right font-[750] tabular-nums">{invoiceMoney(invoice)}</td>
          <td className="whitespace-nowrap px-4 py-4 text-xs text-[#6d7c74]">{invoiceDate(invoice.due_at)}</td>
          <td className="px-4 py-4"><div className="flex flex-wrap gap-2"><button type="button" className="secondary min-h-9 px-3" onClick={() => void copyInvoice(invoice)}><Icon name="check" className="size-4" />{copied === invoice.id ? "Copied" : "Copy"}</button>{smsLink && <a className="secondary min-h-9 px-3 no-underline" href={smsLink}><Icon name="arrow" className="size-4" />SMS</a>}</div></td>
        </tr>;
      })}</tbody>
    </table></div></div> : <EmptyState icon="invoices" title="No invoices yet" description="Create an invoice for a service, send the link to your client, and LynxPay will update it when M-PESA payment evidence arrives." />}
    {!loading && nextBefore && <div className="mt-5 flex justify-center"><button className="secondary" onClick={() => void load({ append: true, before: nextBefore })}>Load older invoices</button></div>}
  </section>;
}
