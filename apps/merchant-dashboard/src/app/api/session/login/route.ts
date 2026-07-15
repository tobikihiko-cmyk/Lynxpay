import { NextRequest, NextResponse } from "next/server";
import { backend, requireSameOrigin, setSession } from "@/lib/bff";

export async function POST(request: NextRequest) {
  try { await requireSameOrigin(); } catch { return NextResponse.json({ detail: "Origin rejected" }, { status: 403 }); }
  const response = await backend("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: await request.text() });
  const payload = await response.json();
  const outgoing = NextResponse.json(response.ok ? { user: payload.user } : payload, { status: response.status });
  if (response.ok) setSession(outgoing, payload);
  return outgoing;
}
