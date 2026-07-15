let storedOnboarding = {};
try { storedOnboarding = JSON.parse(sessionStorage.getItem("lynxpay_onboarding") || "{}"); }
catch (_error) { storedOnboarding = {}; }

const state = {
  token: sessionStorage.getItem("lynxpay_token") || "",
  refreshToken: sessionStorage.getItem("lynxpay_refresh_token") || "",
  baseUrl: location.origin,
  merchants: [],
  onboarding: { step: 1, ...storedOnboarding },
  issuedApiKey: "",
  user: null,
};

const content = document.querySelector("#content");
const title = document.querySelector("#title");
const eyebrow = document.querySelector("#eyebrow");
const status = document.querySelector("#status");
const nav = document.querySelector("#nav");
const logout = document.querySelector("#logout");
const sessionMeta = document.querySelector("#session-meta");
const userName = document.querySelector("#user-name");
const userRole = document.querySelector("#user-role");
const userOrb = document.querySelector("#user-orb");
const networkCard = document.querySelector("#network-card");
const networkEnvironment = document.querySelector("#network-environment");

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  })[character]);
}

function pageHeading(name, context = "M-PESA OPERATIONS", authenticated = true) {
  title.textContent = name;
  eyebrow.textContent = context;
  document.body.classList.toggle("auth-mode", !authenticated);
  sessionMeta.hidden = !authenticated;
}

function humanize(value) {
  return String(value || "unknown").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusPill(value) {
  const normalized = String(value || "unknown").toLowerCase().replace(/[^a-z0-9_]/g, "");
  return `<span class="status-pill status-${normalized}">${escapeHtml(humanize(value))}</span>`;
}

function formatMoney(value, currency = "KES") {
  const amount = Number(value || 0);
  return `${escapeHtml(currency)} ${new Intl.NumberFormat("en-KE", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(amount)}`;
}

function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return escapeHtml(value);
  return new Intl.DateTimeFormat("en-KE", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
}

function maskPhone(value) {
  const phone = String(value || "");
  return phone.length >= 9 ? `${phone.slice(0, 6)}•••${phone.slice(-3)}` : phone || "—";
}

function shortId(value, length = 12) {
  const identifier = String(value || "");
  return identifier.length > length ? `${identifier.slice(0, length)}…` : identifier || "—";
}

function pageLead(titleText, description, action = "") {
  return `<div class="page-lead"><div><span class="eyeline">Operational view</span><h2>${escapeHtml(titleText)}</h2><p>${description}</p></div>${action}</div>`;
}

function emptyState(code, heading, description) {
  return `<div class="empty-state"><span>${escapeHtml(code)}</span><h3>${escapeHtml(heading)}</h3><p>${escapeHtml(description)}</p></div>`;
}

function updateSessionChrome() {
  if (!state.user) return;
  userName.textContent = state.user.full_name || state.user.email;
  userRole.textContent = state.user.role || "owner";
  userOrb.textContent = String(state.user.full_name || "LP").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function flash(message, error = false) {
  status.textContent = message;
  status.classList.toggle("error", error);
}

async function api(path, options = {}, allowRefresh = true) {
  const response = await fetch(`${state.baseUrl}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(state.token ? { Authorization: `Bearer ${state.token}` } : {}),
      ...(options.headers || {}),
    },
  });
  if (response.status === 401 && allowRefresh && state.refreshToken && !path.startsWith("/api/v1/auth/")) {
    const refreshed = await api("/api/v1/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token: state.refreshToken }),
    }, false);
    state.token = refreshed.access_token;
    state.refreshToken = refreshed.refresh_token;
    sessionStorage.setItem("lynxpay_token", state.token);
    sessionStorage.setItem("lynxpay_refresh_token", state.refreshToken);
    return api(path, options, false);
  }
  const payload = response.status === 204 ? null : await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload?.detail || `Request failed (${response.status})`);
  return payload;
}

function merchantOptions() {
  return state.merchants.map((merchant) => `<option value="${escapeHtml(merchant.id)}">${escapeHtml(merchant.merchant_name)} · ${escapeHtml(merchant.shortcode)}</option>`).join("");
}

async function loadMerchants() {
  const result = await api("/api/v1/merchants");
  state.merchants = result.items || [];
  networkCard.hidden = false;
  const hasProduction = state.merchants.some((merchant) => merchant.environment === "production");
  const activeCount = state.merchants.filter((merchant) => merchant.status === "active").length;
  networkEnvironment.textContent = state.merchants.length
    ? `${hasProduction ? "Live" : "Sandbox"} · ${activeCount} active account${activeCount === 1 ? "" : "s"}`
    : "No M-PESA account configured";
}

function bindForm(selector, handler) {
  document.querySelector(selector)?.addEventListener("submit", async (event) => {
    event.preventDefault();
    content.setAttribute("aria-busy", "true");
    flash("Working…");
    try {
      const message = await handler(new FormData(event.currentTarget));
      if (message !== false) flash(typeof message === "string" ? message : "Saved");
    }
    catch (error) { flash(error.message, true); }
    finally { content.setAttribute("aria-busy", "false"); }
  });
}

function renderLogin() {
  pageHeading("Secure sign in", "MERCHANT ACCESS", false);
  nav.hidden = true;
  logout.hidden = true;
  networkCard.hidden = true;
  content.innerHTML = `<div class="auth-stage">
    <section class="auth-story" aria-labelledby="auth-story-title">
      <span class="signal">Built for Safaricom Daraja</span>
      <h2 id="auth-story-title">Payment certainty, engineered for <span class="no-break">M-PESA.</span></h2>
      <p>LynxPay gives your team one operational truth for every STK request, callback, receipt and reconciliation event—using credentials owned by your business.</p>
      <div class="trust-grid">
        <div class="trust-item"><strong>Raw evidence</strong><small>Every callback retained</small></div>
        <div class="trust-item"><strong>Safe state</strong><small>No premature success</small></div>
        <div class="trust-item"><strong>Tenant locked</strong><small>Your account, your keys</small></div>
      </div>
    </section>
    <div class="card auth-card"><div class="card-head"><span class="eyeline">LynxPay control room</span><h2>Welcome back</h2><p>Authenticate to enter your M-PESA operations workspace.</p></div><form id="login-form">
      <label>Work email<input name="email" type="email" autocomplete="username" placeholder="you@business.co.ke" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" placeholder="Your secure password" required></label>
      <label>MFA or recovery code <span class="hint">Required when enabled</span><input name="mfa_code" inputmode="numeric" autocomplete="one-time-code" placeholder="000000"></label>
      <button class="primary" type="submit">Enter LynxPay</button>
      <div class="form-links"><button class="text-button" id="create-account" type="button">Open a merchant account</button><button class="text-button" id="forgot-password" type="button">Reset password</button></div>
      <p class="hint">Protected session · credentials and payment evidence are never stored in this page.</p>
    </form></div>
  </div>`;
  bindForm("#login-form", async (data) => {
    const result = await api("/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password"), mfa_code: data.get("mfa_code") || null }) }, false);
    state.token = result.access_token;
    state.refreshToken = result.refresh_token;
    sessionStorage.setItem("lynxpay_token", state.token);
    sessionStorage.setItem("lynxpay_refresh_token", state.refreshToken);
    await start();
    return false;
  });
  document.querySelector("#forgot-password").addEventListener("click", renderForgotPassword);
  document.querySelector("#create-account").addEventListener("click", renderRegistration);
}

function renderRegistration() {
  state.onboarding = { step: 1 };
  saveOnboarding();
  renderOnboarding(1);
}

const onboardingSteps = [
  "Create account", "Business profile", "M-PESA setup",
  "Daraja credentials", "Test payment", "Activation",
];

function saveOnboarding(changes = {}) {
  state.onboarding = { ...state.onboarding, ...changes };
  sessionStorage.setItem("lynxpay_onboarding", JSON.stringify(state.onboarding));
}

function wizardLayout(step, body) {
  const steps = onboardingSteps.map((label, index) => {
    const number = index + 1;
    const current = number === step ? ' aria-current="step"' : "";
    const stateClass = number < step ? " complete" : number === step ? " current" : "";
    return `<li class="wizard-step${stateClass}"${current}><span>${number}</span>${escapeHtml(label)}</li>`;
  }).join("");
  return `<div class="wizard"><ol class="wizard-steps" aria-label="Merchant onboarding progress">${steps}</ol><div class="card wizard-card">${body}</div></div>`;
}

function goToOnboardingStep(step, changes = {}) {
  saveOnboarding({ ...changes, step });
  return renderOnboarding(step);
}

async function renderOnboarding(step = state.onboarding.step || 1) {
  saveOnboarding({ step });
  pageHeading(`Merchant activation · ${step} of 6`, "DARAJA LAUNCH SEQUENCE", step !== 1);
  nav.hidden = step === 1;
  logout.hidden = step === 1;
  if (step === 1) return renderOnboardingAccount();
  if (!state.token) return renderLogin();
  nav.hidden = false;
  logout.hidden = false;
  if (step === 2) return renderOnboardingBusiness();
  if (step === 3) return renderOnboardingMpesa();
  if (step === 4) return renderOnboardingCredentials();
  if (step === 5) return renderOnboardingTestPayment();
  return renderOnboardingActivation();
}

function renderOnboardingAccount() {
  content.innerHTML = wizardLayout(1, `<form id="onboarding-account-form">
    <h2>Create the owner account</h2><p class="hint">This owner controls one LynxPay organization. Your merchant will always use its own M-PESA credentials.</p>
    <label>Business name<input name="organization_name" autocomplete="organization" required minlength="2"></label>
    <label>Owner full name<input name="full_name" autocomplete="name" required minlength="2"></label>
    <label>Email<input name="contact_email" type="email" autocomplete="email" required></label>
    <label>Phone<input name="contact_phone" autocomplete="tel" placeholder="0712345678" required></label>
    <label>Password<input name="password" type="password" minlength="12" autocomplete="new-password" required></label>
    <div class="actions"><button class="primary" type="submit">Create account and continue</button><button class="secondary" id="back-login" type="button">Back to sign in</button></div>
  </form>`);
  bindForm("#onboarding-account-form", async (data) => {
    const result = await api("/api/v1/auth/register", {
      method: "POST", body: JSON.stringify(Object.fromEntries(data)),
    }, false);
    state.token = result.access_token;
    state.refreshToken = result.refresh_token;
    sessionStorage.setItem("lynxpay_token", state.token);
    sessionStorage.setItem("lynxpay_refresh_token", state.refreshToken);
    await goToOnboardingStep(2);
    flash("Owner account created. Complete the business profile.");
    return false;
  });
  document.querySelector("#back-login").addEventListener("click", renderLogin);
}

async function renderOnboardingBusiness() {
  const organization = await api("/api/v1/organization");
  content.innerHTML = wizardLayout(2, `<form id="onboarding-business-form">
    <h2>Business profile</h2><p class="hint">Tell us which business this organization represents.</p>
    <label>Legal name<input name="legal_name" value="${escapeHtml(organization.legal_name || "")}" autocomplete="organization" required minlength="2"></label>
    <label>Business type<select name="business_type" required>
      <option value="">Select business type</option><option>Retail</option><option>Restaurant</option><option>Hospitality</option><option>Professional services</option><option>E-commerce</option><option>Other</option>
    </select></label>
    <div class="field-grid"><label>County<input name="county" value="${escapeHtml(organization.county || "")}" required></label><label>Town<input name="town" value="${escapeHtml(organization.town || "")}" required></label></div>
    <label>Contact phone<input name="contact_phone" value="${escapeHtml(organization.contact_phone || "")}" autocomplete="tel" required></label>
    <label>Support email<input name="support_email" value="${escapeHtml(organization.support_email || organization.contact_email)}" type="email" autocomplete="email" required></label>
    <button class="primary" type="submit">Save profile and continue</button>
  </form>`);
  const businessType = document.querySelector('[name="business_type"]');
  if (organization.business_type) {
    const supported = [...businessType.options].some((option) => option.value === organization.business_type);
    businessType.value = supported ? organization.business_type : "Other";
  }
  bindForm("#onboarding-business-form", async (data) => {
    await api("/api/v1/organization", { method: "PATCH", body: JSON.stringify(Object.fromEntries(data)) });
    await goToOnboardingStep(3);
    return false;
  });
}

async function renderOnboardingMpesa() {
  await loadMerchants();
  let merchant = state.merchants.find((item) => item.id === state.onboarding.merchantId);
  if (!merchant && state.merchants.length === 1) {
    merchant = state.merchants[0];
    saveOnboarding({ merchantId: merchant.id });
  }
  if (merchant) {
    content.innerHTML = wizardLayout(3, `<h2>M-PESA merchant</h2><dl class="summary-list"><dt>Merchant</dt><dd>${escapeHtml(merchant.merchant_name)}</dd><dt>Type</dt><dd>${escapeHtml(merchant.shortcode_type)}</dd><dt>Shortcode</dt><dd>${escapeHtml(merchant.shortcode)}</dd><dt>Environment</dt><dd>${escapeHtml(merchant.environment)}</dd></dl><button class="primary" id="merchant-continue">Continue to credentials</button>`);
    document.querySelector("#merchant-continue").addEventListener("click", () => goToOnboardingStep(4));
    return;
  }
  content.innerHTML = wizardLayout(3, `<form id="onboarding-mpesa-form">
    <h2>Connect your M-PESA account</h2><p class="hint">LynxPay does not aggregate funds. This must be your business's own PayBill, Till, or store number.</p>
    <label>Merchant display name<input name="merchant_name" required></label>
    <label>Business shortcode<input name="shortcode" inputmode="numeric" required></label>
    <label>Account type<select name="shortcode_type" id="onboarding-merchant-type"><option value="paybill">PayBill</option><option value="till">Till</option><option value="store_number">Store number</option></select></label>
    <label id="onboarding-till-field" hidden>Till/store number<input name="till_number" inputmode="numeric"></label>
    <label>Environment<select name="environment" id="onboarding-environment"><option value="sandbox">Sandbox</option><option value="production">Production (live money)</option></select></label>
    <div class="live-warning" id="onboarding-live-warning" role="note"></div>
    <button class="primary" type="submit">Create merchant and continue</button>
  </form>`);
  const type = document.querySelector("#onboarding-merchant-type");
  const tillField = document.querySelector("#onboarding-till-field");
  const tillInput = tillField.querySelector("input");
  const syncTill = () => { const required = type.value !== "paybill"; tillField.hidden = !required; tillInput.required = required; };
  type.addEventListener("change", syncTill); syncTill();
  const environment = document.querySelector("#onboarding-environment");
  const warning = document.querySelector("#onboarding-live-warning");
  const syncEnvironment = () => { warning.textContent = environment.value === "production" ? "LIVE MODE: the KES 1 verification prompt uses real money and real customer credentials." : "Sandbox mode uses Safaricom test credentials and does not collect live funds."; };
  environment.addEventListener("change", syncEnvironment); syncEnvironment();
  bindForm("#onboarding-mpesa-form", async (data) => {
    const body = Object.fromEntries(data); if (!body.till_number) delete body.till_number;
    const created = await api("/api/v1/merchants", { method: "POST", body: JSON.stringify(body) });
    await goToOnboardingStep(4, { merchantId: created.id, environment: created.environment });
    return false;
  });
}

async function renderOnboardingCredentials() {
  const merchant = await api(`/api/v1/merchants/${encodeURIComponent(state.onboarding.merchantId)}`);
  if (["verified", "active"].includes(merchant.status)) {
    content.innerHTML = wizardLayout(4, `<h2>Daraja credentials verified</h2><p class="success-note">OAuth authentication succeeded for this ${escapeHtml(merchant.environment)} merchant.</p><label>Callback URL<input value="${escapeHtml(merchant.callback_url)}" readonly></label><button class="primary" id="credentials-continue">Continue to KES 1 test</button>`);
    document.querySelector("#credentials-continue").addEventListener("click", () => goToOnboardingStep(5));
    return;
  }
  content.innerHTML = wizardLayout(4, `<form id="onboarding-credentials-form">
    <h2>Daraja credentials</h2><p class="hint">Secrets are encrypted immediately and never returned in plaintext.</p>
    <label>Consumer key<input name="consumer_key" type="password" autocomplete="off" required></label>
    <label>Consumer secret<input name="consumer_secret" type="password" autocomplete="off" required></label>
    <label>Passkey<input name="passkey" type="password" autocomplete="off" required></label>
    <label>Callback URL<input value="${escapeHtml(merchant.callback_url)}" readonly></label>
    <button class="primary" type="submit">Encrypt, test credentials, and continue</button>
  </form>`);
  bindForm("#onboarding-credentials-form", async (data) => {
    await api(`/api/v1/merchants/${merchant.id}/daraja-credentials`, { method: "POST", body: JSON.stringify({ consumer_key: data.get("consumer_key"), consumer_secret: data.get("consumer_secret"), passkey: data.get("passkey"), shortcode: merchant.shortcode, environment: merchant.environment }) });
    await api(`/api/v1/merchants/${merchant.id}/daraja-credentials/test`, { method: "POST", body: "{}" });
    await goToOnboardingStep(5, { environment: merchant.environment });
    flash("Credentials verified. Send the KES 1 callback test.");
    return false;
  });
}

async function renderOnboardingTestPayment() {
  const merchant = await api(`/api/v1/merchants/${encodeURIComponent(state.onboarding.merchantId)}`);
  let payment = null;
  if (state.onboarding.paymentId) {
    try { payment = await api(`/api/v1/payments/${encodeURIComponent(state.onboarding.paymentId)}`); }
    catch (_error) { saveOnboarding({ paymentId: null }); }
  }
  if (payment?.status === "success") {
    content.innerHTML = wizardLayout(5, `<h2>Callback confirmed</h2><p class="success-note">KES 1 received and verified with receipt ${escapeHtml(payment.mpesa_receipt_number)}.</p><dl class="summary-list"><dt>Payment</dt><dd>${escapeHtml(payment.id)}</dd><dt>Status</dt><dd>${escapeHtml(payment.status)}</dd><dt>Receipt</dt><dd>${escapeHtml(payment.mpesa_receipt_number)}</dd></dl><button class="primary" id="test-continue">Continue to activation</button>`);
    document.querySelector("#test-continue").addEventListener("click", () => goToOnboardingStep(6));
    return;
  }
  const pending = payment && ["created", "pending", "stk_sent", "unknown"].includes(payment.status);
  content.innerHTML = wizardLayout(5, `<h2>KES 1 verification payment</h2><p class="hint">Send one STK prompt and wait for its callback. An accepted prompt is not proof of payment.</p>
    ${payment ? `<div class="payment-check"><span class="pill">${escapeHtml(payment.status)}</span><p>${pending ? "Waiting for a verified M-PESA callback. Do not send another STK request while acceptance is uncertain." : "This attempt did not succeed. You may start a new verification attempt."}</p></div>` : ""}
    ${pending ? '<button class="primary" id="check-verification" type="button">Check callback status</button>' : `<form id="onboarding-test-form"><label>Phone receiving the STK prompt<input name="phone_number" autocomplete="tel" placeholder="0712345678" required></label><button class="primary" type="submit">Send KES 1 STK Push</button></form>`}
  `);
  document.querySelector("#check-verification")?.addEventListener("click", () => renderOnboardingTestPayment());
  bindForm("#onboarding-test-form", async (data) => {
    const nonce = crypto.randomUUID();
    const result = await api("/api/v1/payments/stk-push", {
      method: "POST", headers: { "Idempotency-Key": `merchant-verification-${merchant.id}-${nonce}` },
      body: JSON.stringify({ merchant_id: merchant.id, amount: 1, phone_number: data.get("phone_number"), external_reference: `VERIFY-${nonce}`, description: "LynxPay merchant verification", purpose: "merchant_verification" }),
    });
    saveOnboarding({ paymentId: result.id });
    await renderOnboardingTestPayment();
    flash(`Verification payment is ${result.status}. Waiting for callback evidence.`);
    return false;
  });
}

async function renderOnboardingActivation() {
  const merchant = await api(`/api/v1/merchants/${encodeURIComponent(state.onboarding.merchantId)}`);
  const payment = state.onboarding.paymentId ? await api(`/api/v1/payments/${encodeURIComponent(state.onboarding.paymentId)}`) : null;
  if (payment?.status !== "success") return goToOnboardingStep(5);
  if (state.issuedApiKey) {
    const example = `curl -X POST ${location.origin}/api/v1/payments/stk-push \\\n+  -H "X-API-Key: ${state.issuedApiKey}" \\\n+  -H "Idempotency-Key: your-order-id" \\\n+  -H "Content-Type: application/json"`;
    content.innerHTML = wizardLayout(6, `<h2>Merchant active</h2><p class="success-note">Your ${escapeHtml(merchant.environment)} merchant is ready for integration.</p><div class="secret-once"><strong>Copy this API key now</strong><code>${escapeHtml(state.issuedApiKey)}</code><p>It is hashed by LynxPay and cannot be shown again.</p></div><h3>Integration guide</h3><pre><code>${escapeHtml(example)}</code></pre><div class="actions"><button class="primary" id="open-payments">Open payments</button><a class="secondary link-button" href="/docs" target="_blank" rel="noopener">API documentation</a></div>`);
    document.querySelector("#open-payments").addEventListener("click", () => { sessionStorage.removeItem("lynxpay_onboarding"); show("payments"); });
    return;
  }
  content.innerHTML = wizardLayout(6, `<h2>Activate merchant</h2><p class="hint">Credentials and callback evidence are verified. Activation creates one merchant-bound ${escapeHtml(merchant.environment)} API key.</p><dl class="summary-list"><dt>Merchant</dt><dd>${escapeHtml(merchant.merchant_name)}</dd><dt>Environment</dt><dd>${escapeHtml(merchant.environment)}</dd><dt>Test receipt</dt><dd>${escapeHtml(payment.mpesa_receipt_number)}</dd></dl><button class="primary" id="activate-merchant">Activate and create API key</button>`);
  document.querySelector("#activate-merchant").addEventListener("click", async () => {
    content.setAttribute("aria-busy", "true");
    try {
      if (merchant.status !== "active") await api(`/api/v1/merchants/${merchant.id}`, { method: "PATCH", body: JSON.stringify({ status: "active" }) });
      const key = await api("/api/v1/api-keys", { method: "POST", body: JSON.stringify({ name: "Onboarding integration", merchant_id: merchant.id, environment: merchant.environment, scopes: ["payments:read", "payments:write", "callbacks:read", "webhooks:write"] }) });
      state.issuedApiKey = key.api_key;
      await renderOnboardingActivation();
      flash("Merchant activated. Copy the API key before leaving this page.");
    } catch (error) { flash(error.message, true); }
    finally { content.setAttribute("aria-busy", "false"); }
  });
}

function renderForgotPassword() {
  pageHeading("Reset access", "MERCHANT ACCESS", false);
  nav.hidden = true;
  logout.hidden = true;
  content.innerHTML = `<div class="card narrow"><form id="forgot-form">
    <label>Email<input name="email" type="email" autocomplete="email" required></label>
    <div class="actions"><button class="primary" type="submit">Send reset instructions</button><button class="secondary" id="back-login" type="button">Back to sign in</button></div>
  </form></div>`;
  bindForm("#forgot-form", async (data) => {
    await api("/api/v1/auth/password-reset/request", { method: "POST", body: JSON.stringify({ email: data.get("email") }) }, false);
    flash("If the account exists, reset instructions have been queued.");
  });
  document.querySelector("#back-login").addEventListener("click", renderLogin);
}

function renderPasswordReset(token) {
  pageHeading("Choose a new password", "SECURE RECOVERY", false);
  nav.hidden = true;
  logout.hidden = true;
  content.innerHTML = `<div class="card narrow"><form id="reset-form">
    <label>New password<input name="password" type="password" minlength="12" autocomplete="new-password" required></label>
    <button class="primary" type="submit">Reset password</button>
  </form></div>`;
  bindForm("#reset-form", async (data) => {
    await api("/api/v1/auth/password-reset/confirm", { method: "POST", body: JSON.stringify({ token, new_password: data.get("password") }) }, false);
    history.replaceState({}, "", location.pathname);
    renderLogin();
    flash("Password reset. Sign in again.");
  });
}

function renderInvitation(token) {
  pageHeading("Join the control room", "TEAM INVITATION", false);
  nav.hidden = true;
  logout.hidden = true;
  content.innerHTML = `<div class="card narrow"><form id="invitation-form">
    <label>Full name<input name="full_name" autocomplete="name" required></label>
    <label>Password<input name="password" type="password" minlength="12" autocomplete="new-password" required></label>
    <button class="primary" type="submit">Join LynxPay</button>
  </form></div>`;
  bindForm("#invitation-form", async (data) => {
    await api(`/api/v1/auth/invitations/${encodeURIComponent(token)}/accept`, { method: "POST", body: JSON.stringify({ full_name: data.get("full_name"), password: data.get("password") }) }, false);
    history.replaceState({}, "", location.pathname);
    renderLogin();
    flash("Invitation accepted. Sign in with your email.");
  });
}

async function renderPayments() {
  pageHeading("M-PESA payments", "MONEY MOVEMENT");
  const result = await api("/api/v1/payments");
  const payments = result.items || [];
  const successful = payments.filter((payment) => payment.status === "success");
  const captured = successful.reduce((sum, payment) => sum + Number(payment.amount || 0), 0);
  const inFlight = payments.filter((payment) => ["created", "pending", "stk_sent"].includes(payment.status)).length;
  const review = payments.filter((payment) => ["unknown", "timeout", "failed"].includes(payment.status)).length;
  const successRate = payments.length ? (successful.length / payments.length) * 100 : 0;
  const rows = payments.map((payment) => `<tr>
    <td><div class="primary-cell"><strong>${escapeHtml(payment.external_reference)}</strong><small>${escapeHtml(humanize(payment.purpose || "payment"))}</small></div></td>
    <td>${statusPill(payment.status)}</td>
    <td class="money">${formatMoney(payment.amount, payment.currency)}</td>
    <td class="mono">${escapeHtml(maskPhone(payment.customer_phone))}</td>
    <td class="mono" title="${escapeHtml(payment.mpesa_receipt_number || "")}">${escapeHtml(shortId(payment.mpesa_receipt_number, 16))}</td>
    <td>${formatDate(payment.created_at)}</td>
  </tr>`).join("");
  content.innerHTML = `${pageLead("One source of truth for every shilling", "STK acceptance is shown as in flight. Only verified callback or reconciliation evidence contributes to captured volume.", '<div class="actions"><button class="primary" id="new-stk" type="button">Send STK Push</button></div>')}
    <div class="metric-grid" aria-label="Payment overview">
      <div class="metric-card"><span class="metric-label">Captured volume <i></i></span><strong class="metric-value">${formatMoney(captured)}</strong><small class="metric-note">Latest ${payments.length} payment records</small></div>
      <div class="metric-card"><span class="metric-label">Success rate <i></i></span><strong class="metric-value">${successRate.toFixed(1)}%</strong><small class="metric-note">Verified success only</small></div>
      <div class="metric-card"><span class="metric-label">In flight <i></i></span><strong class="metric-value">${inFlight}</strong><small class="metric-note">Awaiting callback evidence</small></div>
      <div class="metric-card"><span class="metric-label">Needs review <i></i></span><strong class="metric-value">${review}</strong><small class="metric-note">Failed, timed out or unknown</small></div>
    </div>
    <div class="surface"><div class="surface-header"><div><h3>Payment ledger</h3><p>Append-only M-PESA payment activity</p></div><span class="surface-meta">Latest ${payments.length} records</span></div>
      ${rows ? `<div class="table-wrap"><table><caption class="sr-only">Merchant M-PESA payments</caption><thead><tr><th>Reference</th><th>State</th><th>Amount</th><th>Customer</th><th>M-PESA receipt</th><th>Initiated</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("KES", "No payment evidence yet", "Send a controlled STK Push to begin building this merchant's payment ledger.")}
    </div>`;
  document.querySelector("#new-stk")?.addEventListener("click", () => show("stk"));
}

async function renderCallbacks() {
  pageHeading("Daraja callback evidence", "PROVIDER EVIDENCE");
  const result = await api("/api/v1/callbacks");
  const callbacks = result.items || [];
  const processed = callbacks.filter((item) => item.processing_status?.startsWith("processed")).length;
  const success = callbacks.filter((item) => item.processing_status === "processed_success").length;
  const conflict = callbacks.filter((item) => ["conflict", "verification_failed"].includes(item.processing_status)).length;
  const unmatched = callbacks.filter((item) => item.processing_status === "unmatched").length;
  const rows = callbacks.map((item) => `<tr>
    <td><div class="primary-cell"><strong class="mono">${escapeHtml(shortId(item.id, 15))}</strong><small>${item.duplicate_of_callback_id ? "Duplicate evidence retained" : "Original provider evidence"}</small></div></td>
    <td>${statusPill(item.processing_status || (item.processed ? "processed" : "received"))}</td>
    <td class="mono" title="${escapeHtml(item.checkout_request_id || "")}">${escapeHtml(shortId(item.checkout_request_id, 18))}</td>
    <td>${escapeHtml(item.result_code ?? "—")}</td>
    <td class="mono">${escapeHtml(shortId(item.mpesa_receipt_number, 16))}</td>
    <td>${formatDate(item.received_at)}</td>
  </tr>`).join("");
  content.innerHTML = `${pageLead("Provider evidence, never overwritten", "Every Daraja callback is retained before validation—including malformed, duplicate, unmatched and conflicting evidence.")}
    <div class="metric-grid" aria-label="Callback overview">
      <div class="metric-card"><span class="metric-label">Callbacks received <i></i></span><strong class="metric-value">${callbacks.length}</strong><small class="metric-note">Latest retained provider events</small></div>
      <div class="metric-card"><span class="metric-label">Processed <i></i></span><strong class="metric-value">${processed}</strong><small class="metric-note">Safely applied outcomes</small></div>
      <div class="metric-card"><span class="metric-label">Success evidence <i></i></span><strong class="metric-value">${success}</strong><small class="metric-note">Verified payment callbacks</small></div>
      <div class="metric-card"><span class="metric-label">Review queue <i></i></span><strong class="metric-value">${conflict + unmatched}</strong><small class="metric-note">Conflicting or unmatched evidence</small></div>
    </div>
    <div class="surface"><div class="surface-header"><div><h3>Callback journal</h3><p>Raw-first Daraja ingestion history</p></div><span class="surface-meta">Integrity protected</span></div>
      ${rows ? `<div class="table-wrap"><table><caption class="sr-only">M-PESA callback evidence</caption><thead><tr><th>Evidence ID</th><th>Handling</th><th>Checkout request</th><th>Result</th><th>Receipt</th><th>Received</th></tr></thead><tbody>${rows}</tbody></table></div>` : emptyState("CB", "No callbacks received", "Daraja callback evidence will appear here as soon as Safaricom reaches the merchant callback URL.")}
    </div>`;
}

async function renderMerchants() {
  pageHeading("M-PESA accounts", "MERCHANT CONFIGURATION");
  await loadMerchants();
  const cards = state.merchants.map((merchant) => `<article class="card merchant-card"><div class="merchant-head"><div><span class="eyeline">${escapeHtml(humanize(merchant.shortcode_type))}</span><h3>${escapeHtml(merchant.merchant_name)}</h3><p class="merchant-id">${escapeHtml(shortId(merchant.id, 20))}</p></div>${statusPill(merchant.status)}</div><div class="merchant-facts"><span><small>Shortcode</small><strong>${escapeHtml(merchant.shortcode)}</strong></span><span><small>Environment</small><strong>${escapeHtml(humanize(merchant.environment))}</strong></span>${merchant.till_number ? `<span><small>Till / store</small><strong>${escapeHtml(merchant.till_number)}</strong></span>` : ""}<span><small>Callback</small><strong title="${escapeHtml(merchant.callback_url)}">${escapeHtml(shortId(merchant.callback_url, 24))}</strong></span></div></article>`).join("");
  content.innerHTML = `${pageLead("Your business, your M-PESA credentials", "Each account is isolated by organization, shortcode and environment. LynxPay never pools credentials or merchant funds.")}
    ${cards ? `<div class="grid">${cards}</div>` : emptyState("MP", "No M-PESA account connected", "Add the business's own PayBill, Till or store number to begin activation.")}
    <div class="split-layout spaced-top"><div class="card"><form id="merchant-form"><div><span class="eyeline">New connection</span><h2>Add M-PESA account</h2><p class="hint">Use credentials issued by Safaricom for this exact business shortcode.</p></div><label>Merchant display name<input name="merchant_name" placeholder="Nairobi flagship PayBill" required></label><div class="field-grid"><label>Business shortcode<input name="shortcode" inputmode="numeric" placeholder="174379" required></label><label>Account type<select name="shortcode_type" id="merchant-type"><option value="paybill">PayBill</option><option value="till">Till</option><option value="store_number">Store number</option></select></label></div><label id="till-field" hidden>Till/store number<input name="till_number" inputmode="numeric"></label><label>Daraja environment<select name="environment" id="merchant-environment"><option value="sandbox">Sandbox</option><option value="production">Production (live money)</option></select></label><div class="live-warning" id="live-warning" role="note"></div><button class="primary">Create M-PESA account</button></form></div>
      <aside class="rail-note"><span class="eyeline">Account isolation</span><h3>No aggregation layer</h3><p>LynxPay initiates directly against the selected business shortcode. Safaricom settles funds to the merchant's own M-PESA account.</p><div class="evidence-flow"><div class="evidence-step"><span>1</span><div><strong>Own credentials</strong><small>Encrypted for one merchant only</small></div></div><div class="evidence-step"><span>2</span><div><strong>Own callback</strong><small>Canonical route per account</small></div></div><div class="evidence-step"><span>3</span><div><strong>Own evidence</strong><small>Tenant-scoped ledger and audit</small></div></div></div></aside>
    </div>`;
  const type = document.querySelector("#merchant-type");
  const tillField = document.querySelector("#till-field");
  const tillInput = tillField.querySelector("input");
  const syncTill = () => { const required = type.value !== "paybill"; tillField.hidden = !required; tillInput.required = required; };
  type.addEventListener("change", syncTill); syncTill();
  const environment = document.querySelector("#merchant-environment");
  const warning = document.querySelector("#live-warning");
  const syncEnvironment = () => { warning.textContent = environment.value === "production" ? "LIVE MONEY · STK prompts and callbacks affect real customer payments. Complete verification before activation." : "SANDBOX · Use Safaricom test credentials and controlled phone numbers."; };
  environment.addEventListener("change", syncEnvironment); syncEnvironment();
  bindForm("#merchant-form", async (data) => { const body = Object.fromEntries(data); if (!body.till_number) delete body.till_number; await api("/api/v1/merchants", { method: "POST", body: JSON.stringify(body) }); await renderMerchants(); });
}

async function renderCredentials() {
  pageHeading("Daraja credentials", "SECURE CONFIGURATION");
  await loadMerchants();
  content.innerHTML = `${pageLead("Credentials that never cross merchant boundaries", "Consumer keys, secrets and passkeys are encrypted immediately, masked in every response and excluded from application logs.")}
    <div class="split-layout"><div class="card"><form id="credentials-form"><div><span class="eyeline">Safaricom developer app</span><h2>Verify Daraja access</h2><p class="hint">A successful OAuth check moves this M-PESA account to verified—not active.</p></div><label>M-PESA account<select name="merchant_id" required>${merchantOptions()}</select></label><label>Consumer key<input name="consumer_key" type="password" autocomplete="off" placeholder="Paste once" required></label><label>Consumer secret<input name="consumer_secret" type="password" autocomplete="off" placeholder="Paste once" required></label><label>Lipa na M-PESA passkey<input name="passkey" type="password" autocomplete="off" placeholder="Paste once" required></label><button class="primary">Encrypt and verify credentials</button></form></div>
    <aside class="rail-note"><span class="eyeline">Secret custody</span><h3>Designed for zero plaintext persistence</h3><p>Credentials are decrypted only for an outbound Daraja operation using the selected merchant account.</p><div class="evidence-flow"><div class="evidence-step"><span>✓</span><div><strong>Envelope encrypted</strong><small>Versioned keys and rotation support</small></div></div><div class="evidence-step"><span>✓</span><div><strong>Response masked</strong><small>Secrets are never returned</small></div></div><div class="evidence-step"><span>✓</span><div><strong>Mutation audited</strong><small>Every credential change retained</small></div></div></div></aside></div>`;
  bindForm("#credentials-form", async (data) => {
    const merchant = state.merchants.find((item) => item.id === data.get("merchant_id"));
    await api(`/api/v1/merchants/${merchant.id}/daraja-credentials`, { method: "POST", body: JSON.stringify({ consumer_key: data.get("consumer_key"), consumer_secret: data.get("consumer_secret"), passkey: data.get("passkey"), shortcode: merchant.shortcode, environment: merchant.environment }) });
    await api(`/api/v1/merchants/${merchant.id}/daraja-credentials/test`, { method: "POST", body: "{}" });
    await goToOnboardingStep(5, { merchantId: merchant.id, environment: merchant.environment, paymentId: null });
    flash("Credentials verified. Complete the KES 1 callback test before activation.");
    return false;
  });
}

async function renderStk() {
  pageHeading("Send STK Push", "M-PESA INITIATION");
  await loadMerchants();
  content.innerHTML = `${pageLead("Initiate with certainty", "One request creates one durable payment identity. Reuse the same external reference only when replaying the exact same intention.")}
    <div class="split-layout"><div class="card"><form id="stk-form"><div><span class="eyeline">Customer prompt</span><h2>Request M-PESA payment</h2><p class="hint">The customer sees an STK prompt from the selected merchant's own shortcode.</p></div><label>M-PESA account<select name="merchant_id" required>${merchantOptions()}</select></label><div class="field-grid"><label>Amount · KES<input name="amount" type="number" min="1" step="1" placeholder="1,000" required></label><label>Customer phone<input name="phone_number" autocomplete="tel" placeholder="0712 345 678" required></label></div><label>External reference<input name="external_reference" placeholder="ORDER-2026-001" required></label><label>Customer-facing description<input name="description" placeholder="Payment for order 001" required></label><button class="primary">Initiate secure STK Push</button></form><div id="stk-result" class="spaced-top" aria-live="polite"></div></div>
      <aside class="rail-note"><span class="eyeline">Evidence sequence</span><h3>Accepted is not paid</h3><p>LynxPay keeps the payment unpaid until Daraja callback evidence or a verified status query confirms success.</p><div class="evidence-flow"><div class="evidence-step"><span>1</span><div><strong>Request persisted</strong><small>Reference and idempotency locked</small></div></div><div class="evidence-step"><span>2</span><div><strong>STK accepted</strong><small>Payment moves to STK sent</small></div></div><div class="evidence-step"><span>3</span><div><strong>Callback verified</strong><small>Only then can payment succeed</small></div></div></div></aside>
    </div>`;
  bindForm("#stk-form", async (data) => {
    const body = Object.fromEntries(data);
    const result = await api("/api/v1/payments/stk-push", { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify(body) });
    document.querySelector("#stk-result").innerHTML = `<div class="success-note"><span class="eyeline">Request accepted</span><h3>${escapeHtml(result.external_reference)}</h3><p>${statusPill(result.status)} Payment identity <strong>${escapeHtml(shortId(result.id))}</strong> is now waiting for Daraja evidence.</p></div>`;
    flash(`STK request created for ${result.external_reference}`);
    return false;
  });
}

const views = { onboarding: () => renderOnboarding(Math.max(state.onboarding.step || 1, 2)), payments: renderPayments, callbacks: renderCallbacks, merchants: renderMerchants, credentials: renderCredentials, stk: renderStk };

async function show(view) {
  document.querySelectorAll("nav button").forEach((button) => button.classList.toggle("active", button.dataset.view === view));
  flash("Loading…");
  try { await views[view](); flash(""); } catch (error) { flash(error.message, true); }
}

async function start() {
  const params = new URLSearchParams(location.search);
  if (params.get("token") && location.pathname.endsWith("reset-password")) return renderPasswordReset(params.get("token"));
  if (params.get("token") && location.pathname.endsWith("accept-invitation")) return renderInvitation(params.get("token"));
  if (!state.token) return renderLogin();
  nav.hidden = false;
  logout.hidden = false;
  try {
    state.user = await api("/api/v1/auth/me");
    updateSessionChrome();
    await loadMerchants();
  } catch (_error) {
    state.user = null;
  }
  if (state.onboarding.step > 1 && state.onboarding.step <= 6) return renderOnboarding(state.onboarding.step);
  await show("payments");
}

nav.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (button) show(button.dataset.view);
});
logout.addEventListener("click", async () => {
  try {
    if (state.refreshToken) await api("/api/v1/auth/logout", { method: "POST", body: JSON.stringify({ refresh_token: state.refreshToken }) }, false);
  } catch (_error) { /* Local sign-out still clears browser credentials. */ }
  sessionStorage.removeItem("lynxpay_token");
  sessionStorage.removeItem("lynxpay_refresh_token");
  state.token = "";
  state.refreshToken = "";
  state.issuedApiKey = "";
  state.user = null;
  sessionMeta.hidden = true;
  networkCard.hidden = true;
  renderLogin();
});
start();
