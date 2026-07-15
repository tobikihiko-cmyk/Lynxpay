import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "LynxPay", template: "%s · LynxPay" },
  description: "M-PESA Daraja payment operations for Kenyan businesses"
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
