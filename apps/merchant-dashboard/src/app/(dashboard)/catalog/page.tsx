"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { CatalogItem } from "@/lib/invoices";
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
type CatalogResponse = { items: CatalogItem[]; limit: number };
type CatalogForm = {
  item_type: "service" | "product";
  name: string;
  description: string;
  unit_price: string;
  sku: string;
};

const blankForm: CatalogForm = {
  item_type: "service",
  name: "",
  description: "",
  unit_price: "",
  sku: ""
};

function money(value: string) {
  return `KES ${Number(value).toLocaleString("en-KE", { maximumFractionDigits: 2 })}`;
}

export default function CatalogPage() {
  const [merchants, setMerchants] = useState<Merchant[]>([]);
  const [merchantId, setMerchantId] = useState("");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [showArchived, setShowArchived] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [form, setForm] = useState<CatalogForm>(blankForm);
  const [editing, setEditing] = useState<Record<string, CatalogForm>>({});

  useEffect(() => {
    let active = true;
    Promise.all([
      api<MerchantPage>("/merchants?limit=100"),
      api<CatalogResponse>("/catalog-items?status=active")
    ]).then(([merchantResult, catalogResult]) => {
      if (!active) return;
      setMerchants(merchantResult.items);
      setItems(catalogResult.items);
      setMerchantId(merchantResult.items.find(item => item.status === "active")?.id || merchantResult.items[0]?.id || "");
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : "Could not load catalog");
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function loadCatalog(nextMerchantId = merchantId, archived = showArchived) {
    setError("");
    setLoading(true);
    try {
      const query = new URLSearchParams();
      if (nextMerchantId) query.set("merchant_id", nextMerchantId);
      query.set("status", archived ? "archived" : "active");
      const result = await api<CatalogResponse>(`/catalog-items?${query}`);
      setItems(result.items);
      setMerchantId(nextMerchantId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load catalog");
    } finally {
      setLoading(false);
    }
  }

  const activeCount = useMemo(() => items.filter(item => item.status === "active" && item.merchant_id === merchantId).length, [items, merchantId]);
  const visibleItems = useMemo(() => items.filter(item => !merchantId || item.merchant_id === merchantId), [items, merchantId]);
  const services = visibleItems.filter(item => item.item_type === "service").length;
  const products = visibleItems.filter(item => item.item_type === "product").length;

  async function createItem(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!merchantId) return;
    setSaving(true);
    setError("");
    try {
      const item = await api<CatalogItem>("/catalog-items", {
        method: "POST",
        body: JSON.stringify({
          merchant_id: merchantId,
          item_type: form.item_type,
          name: form.name.trim(),
          ...(form.description.trim() ? { description: form.description.trim() } : {}),
          unit_price: form.unit_price,
          ...(form.sku.trim() ? { sku: form.sku.trim() } : {})
        })
      });
      setItems(current => [item, ...current]);
      setForm(blankForm);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save catalog item");
    } finally {
      setSaving(false);
    }
  }

  function beginEdit(item: CatalogItem) {
    setEditing(current => ({
      ...current,
      [item.id]: {
        item_type: item.item_type,
        name: item.name,
        description: item.description || "",
        unit_price: item.unit_price,
        sku: item.sku || ""
      }
    }));
  }

  async function saveEdit(item: CatalogItem) {
    const draft = editing[item.id];
    if (!draft) return;
    setSaving(true);
    setError("");
    try {
      const updated = await api<CatalogItem>(`/catalog-items/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          item_type: draft.item_type,
          name: draft.name.trim(),
          description: draft.description.trim(),
          unit_price: draft.unit_price,
          sku: draft.sku.trim()
        })
      });
      setItems(current => current.map(row => row.id === item.id ? updated : row));
      setEditing(current => {
        const next = { ...current };
        delete next[item.id];
        return next;
      });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update catalog item");
    } finally {
      setSaving(false);
    }
  }

  async function setItemStatus(item: CatalogItem, status: "active" | "archived") {
    setSaving(true);
    setError("");
    try {
      const updated = await api<CatalogItem>(`/catalog-items/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status })
      });
      if (showArchived || status === "active") {
        setItems(current => current.map(row => row.id === item.id ? updated : row));
      } else {
        setItems(current => current.filter(row => row.id !== item.id));
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not update catalog item");
    } finally {
      setSaving(false);
    }
  }

  return <section>
    <PageHeader eyebrow="Merchant setup" title="Catalog" description="Save this merchant's own services and products with their real prices. Invoices can use these saved items, or stay fully manual." />

    <div className="my-7 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <MetricCard label="Active slots" value={`${activeCount}/20`} detail="Maximum active items per merchant" icon="invoices" tone={activeCount >= 20 ? "warning" : "neutral"} />
      <MetricCard label="Services" value={String(services)} detail="Consultations, labour, sessions, procedures" icon="check" />
      <MetricCard label="Products" value={String(products)} detail="Parts, supplies, add-ons, retail goods" icon="payments" />
      <MetricCard label="Mode" value={showArchived ? "Archived" : "Active"} detail="Switch views without mixing merchants" icon="clock" />
    </div>

    <div className="grid gap-6 xl:grid-cols-[420px_minmax(0,1fr)]">
      <form onSubmit={createItem} className="surface grid gap-4 p-5 md:p-6">
        <div className="flex items-center justify-between gap-3">
          <div><p className="eyeline">Add item</p><h3 className="mt-2 text-xl font-[740] tracking-[-.035em]">Merchant-owned price list</h3></div>
          <span className="rounded-full border border-[#dfe6e1] bg-[#fbfcfb] px-3 py-1 text-[11px] font-black text-[#607168]">{activeCount}/20</span>
        </div>
        <label className="field">Merchant
          <select value={merchantId} onChange={event => void loadCatalog(event.target.value, showArchived)} required>
            <option value="">Select merchant</option>
            {merchants.map(merchant => <option key={merchant.id} value={merchant.id}>{merchant.merchant_name} · {merchant.shortcode_type} {merchant.shortcode}</option>)}
          </select>
        </label>
        <div className="grid grid-cols-[130px_1fr] gap-3">
          <label className="field">Type
            <select value={form.item_type} onChange={event => setForm({ ...form, item_type: event.target.value as CatalogForm["item_type"] })}>
              <option value="service">Service</option>
              <option value="product">Product</option>
            </select>
          </label>
          <label className="field">Name
            <input value={form.name} onChange={event => setForm({ ...form, name: event.target.value })} placeholder="Adult haircut" required />
          </label>
        </div>
        <div className="grid grid-cols-[1fr_130px] gap-3">
          <label className="field">Description
            <input value={form.description} onChange={event => setForm({ ...form, description: event.target.value })} placeholder="Optional" />
          </label>
          <label className="field">Price
            <input inputMode="numeric" value={form.unit_price} onChange={event => setForm({ ...form, unit_price: event.target.value })} placeholder="500" required />
          </label>
        </div>
        <label className="field">SKU or code
          <input value={form.sku} onChange={event => setForm({ ...form, sku: event.target.value })} placeholder="Optional" />
        </label>
        <button className="primary" type="submit" disabled={saving || !merchantId || activeCount >= 20}><Icon name="invoices" className="size-4" />Add item</button>
        <p className="text-xs leading-5 text-[#6d7c74]">Use real merchant pricing. A barber shop, salon, dentist, tax consultant, or law firm builds only its own catalog.</p>
      </form>

      <section>
        <div className="mb-4 rounded-2xl border border-[#dfe6e1] bg-white p-3 shadow-[0_1px_2px_rgb(7_21_14/.03)]">
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={!showArchived ? "primary" : "secondary"} onClick={() => { setShowArchived(false); void loadCatalog(merchantId, false); }}>Active</button>
            <button type="button" className={showArchived ? "primary" : "secondary"} onClick={() => { setShowArchived(true); void loadCatalog(merchantId, true); }}>Archived</button>
          </div>
        </div>

        {error && <div role="alert" className="mb-4 rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800">{error}</div>}
        {loading ? <LoadingTable /> : visibleItems.length ? <div className="surface overflow-hidden"><div className="overflow-x-auto">
          <table className="w-full min-w-[940px] border-collapse text-left text-sm">
            <thead><tr className="border-b border-[#e4eae6] bg-[#fafbfa] text-[9px] font-black uppercase tracking-[.14em] text-[#849189]"><th className="px-5 py-3.5">Item</th><th className="px-4 py-3.5">Type</th><th className="px-4 py-3.5 text-right">Price</th><th className="px-4 py-3.5">Status</th><th className="px-4 py-3.5">Actions</th></tr></thead>
            <tbody>{visibleItems.map(item => {
              const draft = editing[item.id];
              return <tr key={item.id} className="border-b border-[#edf1ee] bg-white last:border-0 align-top">
                <td className="px-5 py-4">{draft ? <div className="grid gap-2"><input className="control min-h-9 text-sm" value={draft.name} onChange={event => setEditing(current => ({ ...current, [item.id]: { ...draft, name: event.target.value } }))} /><input className="control min-h-9 text-sm" value={draft.description} onChange={event => setEditing(current => ({ ...current, [item.id]: { ...draft, description: event.target.value } }))} /></div> : <><strong className="block">{item.name}</strong><span className="mt-1 block max-w-md text-[11px] text-[#849189]">{item.description || item.sku || "No description"}</span></>}</td>
                <td className="px-4 py-4">{draft ? <select className="control min-h-9 text-sm capitalize" value={draft.item_type} onChange={event => setEditing(current => ({ ...current, [item.id]: { ...draft, item_type: event.target.value as CatalogForm["item_type"] } }))}><option value="service">Service</option><option value="product">Product</option></select> : <span className="capitalize">{item.item_type}</span>}</td>
                <td className="px-4 py-4 text-right">{draft ? <input className="control ml-auto min-h-9 max-w-32 text-right text-sm" inputMode="numeric" value={draft.unit_price} onChange={event => setEditing(current => ({ ...current, [item.id]: { ...draft, unit_price: event.target.value } }))} /> : <strong className="tabular-nums">{money(item.unit_price)}</strong>}</td>
                <td className="px-4 py-4"><StatusPill status={item.status} /></td>
                <td className="px-4 py-4"><div className="flex flex-wrap gap-2">{draft ? <><button className="primary min-h-9 px-3" type="button" disabled={saving} onClick={() => void saveEdit(item)}>Save</button><button className="secondary min-h-9 px-3" type="button" onClick={() => setEditing(current => { const next = { ...current }; delete next[item.id]; return next; })}>Cancel</button></> : <><button className="secondary min-h-9 px-3" type="button" onClick={() => beginEdit(item)}>Edit</button>{item.status === "active" ? <button className="quiet-button min-h-9 px-3" type="button" disabled={saving} onClick={() => void setItemStatus(item, "archived")}>Archive</button> : <button className="quiet-button min-h-9 px-3" type="button" disabled={saving} onClick={() => void setItemStatus(item, "active")}>Restore</button>}</>}</div></td>
              </tr>;
            })}</tbody>
          </table>
        </div></div> : <EmptyState icon="invoices" title="No catalog items in this view" description="Add this merchant's actual services or products. Invoices can still be created manually without catalog items." />}
      </section>
    </div>
  </section>;
}
