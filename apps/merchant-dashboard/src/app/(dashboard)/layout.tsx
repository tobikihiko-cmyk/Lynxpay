import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Shell } from "@/components/shell";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  if (!(await cookies()).has("lp_refresh")) redirect("/sign-in");
  return <Shell>{children}</Shell>;
}
