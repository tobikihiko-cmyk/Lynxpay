export type CatalogItem = {
  id: string;
  merchant_id: string;
  item_type: "service" | "product";
  name: string;
  description?: string | null;
  unit_price: string;
  currency: string;
  sku?: string | null;
  status: string;
  sort_order: number;
};

export type InvoiceLineItem = {
  id: string;
  catalog_item_id?: string | null;
  position: number;
  item_type: "service" | "product" | "custom";
  name: string;
  description?: string | null;
  quantity: string;
  unit_price: string;
  line_total: string;
};

export type Invoice = {
  id: string;
  merchant_id: string;
  invoice_number: string;
  public_id: string;
  payment_link: string;
  client_name: string;
  client_phone?: string | null;
  client_email?: string | null;
  service_title: string;
  description: string;
  amount: string;
  currency: string;
  status: string;
  due_at?: string | null;
  sent_at?: string | null;
  paid_at?: string | null;
  voided_at?: string | null;
  payment_id?: string | null;
  merchant_display_name: string;
  merchant_display_address?: string | null;
  merchant_display_email?: string | null;
  merchant_display_phone?: string | null;
  memo?: string | null;
  line_items: InvoiceLineItem[];
  created_at: string;
};

export type PublicInvoice = {
  public_id: string;
  invoice_number: string;
  client_name: string;
  service_title: string;
  description: string;
  amount: string;
  currency: string;
  status: string;
  due_at?: string | null;
  paid_at?: string | null;
  line_items: InvoiceLineItem[];
  merchant: {
    name: string;
    address?: string | null;
    email?: string | null;
    phone?: string | null;
    shortcode_type?: string | null;
    shortcode?: string | null;
    till_number?: string | null;
  };
};

const formatter = new Intl.NumberFormat("en-KE", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 2
});

export function invoiceMoney(invoice: Pick<Invoice | PublicInvoice, "amount" | "currency">) {
  return `${invoice.currency || "KES"} ${formatter.format(Number(invoice.amount))}`;
}

export function invoiceDate(value?: string | null) {
  if (!value) return "No due date";
  return new Intl.DateTimeFormat("en-KE", {
    day: "2-digit",
    month: "short",
    year: "numeric"
  }).format(new Date(value));
}

export function absoluteInvoiceLink(link: string) {
  if (link.startsWith("http")) return link;
  if (typeof window === "undefined") return link;
  return `${window.location.origin}${link}`;
}
