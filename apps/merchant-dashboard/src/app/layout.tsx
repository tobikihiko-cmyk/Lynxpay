import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "LynxPay — M-PESA operations", template: "%s · LynxPay" },
  description: "The operational control plane for your M-PESA Daraja payments",
  applicationName: "LynxPay",
  robots: { index: false, follow: false }
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en" suppressHydrationWarning><body>{children}</body></html>;
}
