import { createHmac } from "node:crypto";
import { expect, test, type BrowserContext, type Page } from "@playwright/test";

const supportURL = process.env.E2E_SUPPORT_URL || "http://127.0.0.1:8091";
const email = `owner-${Date.now()}@lynxpay-e2e.co.ke`;
const password = "correct-horse-battery-staple";
let mfaSecret = "";

function decodeBase32(value: string): Buffer {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  const bits = value
    .replaceAll("=", "")
    .toUpperCase()
    .split("")
    .map((character) => alphabet.indexOf(character).toString(2).padStart(5, "0"))
    .join("");
  const bytes = [];
  for (let offset = 0; offset + 8 <= bits.length; offset += 8) {
    bytes.push(Number.parseInt(bits.slice(offset, offset + 8), 2));
  }
  return Buffer.from(bytes);
}

function totp(secret: string, at = Date.now()): string {
  const counter = Math.floor(at / 30_000);
  const message = Buffer.alloc(8);
  message.writeBigUInt64BE(BigInt(counter));
  const digest = createHmac("sha1", decodeBase32(secret)).update(message).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const binary =
    ((digest[offset] & 0x7f) << 24) |
    ((digest[offset + 1] & 0xff) << 16) |
    ((digest[offset + 2] & 0xff) << 8) |
    (digest[offset + 3] & 0xff);
  return String(binary % 1_000_000).padStart(6, "0");
}

async function dashboardApi<T>(
  page: Page,
  path: string,
  init: { method?: string; body?: unknown; headers?: Record<string, string> } = {},
): Promise<T> {
  return page.evaluate(
    async ({ target, options }) => {
      const response = await fetch(`/api/lynxpay${target}`, {
        method: options.method || "GET",
        headers: {
          "Content-Type": "application/json",
          ...(options.headers || {}),
        },
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
      });
      const payload = response.status === 204 ? null : await response.json();
      if (!response.ok) throw new Error(`${response.status}: ${JSON.stringify(payload)}`);
      return payload;
    },
    { target: path, options: init },
  ) as Promise<T>;
}

async function latestEmail(page: Page, template: string): Promise<{ url: string }> {
  await expect
    .poll(
      async () => {
        const response = await page.request.get(
          `${supportURL}/emails/latest?to=${encodeURIComponent(email)}&template=${template}`,
        );
        return response.status();
      },
      { timeout: 45_000 },
    )
    .toBe(200);
  return (
    await page.request.get(
      `${supportURL}/emails/latest?to=${encodeURIComponent(email)}&template=${template}`,
    )
  ).json();
}

async function expireAccessCookie(context: BrowserContext): Promise<void> {
  const existing = (await context.cookies()).find((cookie) => cookie.name === "lp_access");
  if (!existing) throw new Error("Active lp_access cookie was not found");
  await context.addCookies([
    {
      name: existing.name,
      value: "expired-access-token",
      domain: existing.domain,
      path: existing.path,
      httpOnly: existing.httpOnly,
      secure: existing.secure,
      sameSite: existing.sameSite,
    },
  ]);
}

test("merchant journey from registration to collection and session controls", async ({
  page,
  context,
}) => {
  test.setTimeout(180_000);

  await test.step("registration and email verification", async () => {
    await page.goto("/sign-up");
    await page.getByLabel("Business name").fill("LynxPay E2E Legal");
    await page.getByLabel("Owner name").fill("James E2E Owner");
    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Kenyan mobile number").fill("0712345678");
    await page.getByLabel("Create password").fill(password);
    await page.getByRole("button", { name: "Create owner account" }).click();
    await expect(page).toHaveURL(/\/verify-email/);

    const verification = await latestEmail(page, "email_verification");
    const verificationURL = new URL(verification.url);
    await page.goto(`${verificationURL.pathname}${verificationURL.search}`);
    await expect(page.getByText("Email verified. Your owner account is protected.")).toBeVisible();
    await page.getByRole("link", { name: "Continue onboarding" }).click();
  });

  await test.step("business and merchant onboarding", async () => {
    const continueToBusiness = page.getByRole("button", {
      name: /Continue to business profile/,
    });
    if (await continueToBusiness.isVisible()) await continueToBusiness.click();
    await page.getByLabel("Registered legal name").fill("LynxPay E2E Legal Limited");
    await page.getByLabel("Business type").selectOption("Professional services");
    await page.getByLabel("County").selectOption("Nairobi");
    await page.getByLabel("Town or locality").fill("Westlands");
    await page.getByLabel("Business phone").fill("0712345678");
    await page.getByLabel("Support email").fill(email);
    await page.getByRole("button", { name: /Save and continue/ }).click();

    await page.getByLabel("Merchant display name").fill("LynxPay E2E PayBill");
    await page.getByLabel("Business shortcode").fill("174379");
    await page.getByRole("button", { name: /Create merchant account/ }).click();
    await expect(page.getByRole("heading", { name: "Secure the Daraja connection" })).toBeVisible();
  });

  await test.step("Daraja credentials and KES 1 callback verification", async () => {
    await page.getByLabel("Consumer key").fill("e2e-consumer-key");
    await page.getByLabel("Consumer secret").fill("e2e-consumer-secret");
    await page.getByLabel("Lipa na M-PESA passkey").fill("e2e-passkey");
    await page.getByRole("button", { name: "Encrypt and store credentials" }).click();
    await expect(page.getByText("Encrypted", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Test Daraja connection" }).click();
    await expect(page.getByRole("heading", { name: "Verify with a KES 1 payment" })).toBeVisible();
    await page.getByRole("button", { name: "Send KES 1 STK Push" }).click();
    await expect(page.getByText("Callback proof received")).toBeVisible({ timeout: 30_000 });

    await page.getByRole("button", { name: /Activation/ }).click();
    await page.getByLabel(/I accept LynxPay Terms/).check();
    await page.getByLabel(/I accept LynxPay Privacy/).check();
    await page.getByRole("button", { name: "Accept current terms" }).click();
    await page.getByRole("button", { name: "Activate sandbox merchant" }).click();
    await expect(page.getByText("Merchant active")).toBeVisible();
  });

  await test.step("MFA enrolment and API-key generation", async () => {
    await page.goto("/team");
    await page.getByRole("button", { name: "Set up authenticator" }).click();
    mfaSecret = await page.getByLabel("Authenticator secret").inputValue();
    await page.getByLabel("Six-digit code").fill(totp(mfaSecret));
    await page.getByRole("button", { name: "Confirm MFA" }).click();
    await expect(page.getByText("MFA is enabled for privileged operations.")).toBeVisible();

    await page.goto("/api-keys");
    await page.getByRole("button", { name: "Create sandbox key" }).click();
    await expect(page.getByText("Copy this key now. It will not be shown again.")).toBeVisible();
  });

  await test.step("invoice creation, STK initiation, and paid status", async () => {
    await page.goto("/invoices");
    await page.getByLabel("Invoice number").fill("E2E-LAW-001");
    await page.getByLabel("Amount").fill("1500");
    await page.getByLabel("Client name").fill("Jane E2E Client");
    await page.getByLabel("Client phone").fill("0712345678");
    await page.getByLabel("Service being paid for").fill("Legal consultation");
    await page.getByLabel("Invoice description").fill("Legal consultation and document review");
    await page.getByRole("button", { name: "Create invoice" }).click();
    await expect(page.getByText("E2E-LAW-001")).toBeVisible();

    const invoicePage = await dashboardApi<{
      items: Array<{ public_id: string; status: string }>;
    }>(page, "/invoices?search=E2E-LAW-001");
    await page.goto(`/pay/${invoicePage.items[0].public_id}`);
    await page.getByLabel("M-PESA phone number").fill("0712345678");
    await page.getByRole("button", { name: "Send M-PESA prompt" }).click();
    await expect(page.getByText("M-PESA prompt sent")).toBeVisible();

    await expect
      .poll(async () => {
        const result = await dashboardApi<{ items: Array<{ status: string }> }>(
          page,
          "/invoices?search=E2E-LAW-001",
        );
        return result.items[0]?.status;
      })
      .toBe("paid");
  });

  await test.step("webhook configuration and team invitation", async () => {
    await page.goto("/webhooks");
    await page.getByLabel("Endpoint URL").fill("https://merchant.example.co.ke/lynxpay");
    await page.getByRole("button", { name: "Create endpoint" }).click();
    await expect(page.getByText("https://merchant.example.co.ke/lynxpay")).toBeVisible();

    await dashboardApi(page, "/team/invitations", {
      method: "POST",
      body: { email: "invited-member@lynxpay-e2e.co.ke", role: "operator" },
    });
    const invitations = await dashboardApi<{ items: Array<{ email: string }> }>(
      page,
      "/team/invitations",
    );
    expect(invitations.items.map((item) => item.email)).toContain(
      "invited-member@lynxpay-e2e.co.ke",
    );
  });

  await test.step("login, refresh rotation, session revocation, and expiry", async () => {
    const cookiesBeforeLogout = await context.cookies();
    await page.getByRole("button", { name: /James E2E Owner/ }).click();
    await expect(page).toHaveURL(/\/sign-in/);

    await page.getByLabel("Work email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByLabel("MFA or recovery code").fill(totp(mfaSecret, Date.now() + 30_000));
    await page.getByRole("button", { name: "Enter LynxPay" }).click();
    await expect(page).toHaveURL(/\/payments/);

    const beforeRefresh = (await context.cookies()).find((cookie) => cookie.name === "lp_refresh");
    await expireAccessCookie(context);
    const me = await dashboardApi<{ email: string }>(page, "/auth/me");
    expect(me.email).toBe(email);
    const afterRefresh = (await context.cookies()).find((cookie) => cookie.name === "lp_refresh");
    expect(afterRefresh?.value).not.toBe(beforeRefresh?.value);

    const sessions = await dashboardApi<{ items: Array<{ id: string }> }>(
      page,
      "/auth/sessions",
    );
    expect(sessions.items.length).toBeGreaterThanOrEqual(1);
    await dashboardApi(page, `/auth/sessions/${sessions.items.at(-1)?.id}`, {
      method: "DELETE",
    });

    await page.request.post(`${supportURL}/sessions/expire-latest?email=${encodeURIComponent(email)}`);
    await expireAccessCookie(context);
    await expect(
      page.evaluate(() => fetch("/api/lynxpay/auth/me").then((response) => response.status)),
    ).resolves.toBe(401);
    await page.reload();
    await expect(page).toHaveURL(/\/sign-in/);
    expect(cookiesBeforeLogout.some((cookie) => cookie.name === "lp_refresh")).toBe(true);
  });
});
