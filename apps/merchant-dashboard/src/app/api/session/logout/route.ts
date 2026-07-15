import { NextResponse } from "next/server";
import { backend, clearSession, requireSameOrigin, sessionTokens } from "@/lib/bff";

export async function POST() {
  try { await requireSameOrigin(); } catch { return NextResponse.json({ detail: "Origin rejected" }, { status: 403 }); }
  const { refresh } = await sessionTokens();
  if (refresh) await backend("/api/v1/auth/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: refresh }) });
  const response = new NextResponse(null, { status: 204 }); clearSession(response); return response;
}
