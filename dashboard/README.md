# LynxPay merchant console

This is the dependency-free LynxPay merchant control room. It calls only the standalone LynxPay API, contains no SmartLynxPOS code, and is intentionally designed around Safaricom Daraja rather than a generic payment-provider abstraction.

```bash
python3 -m http.server 3000 --directory dashboard
```

The production-style Nginx configuration proxies same-origin `/api/` requests to the API service. The console covers the six-step registration/onboarding wizard, encrypted credential verification, KES 1 callback proof, merchant activation, one-time API-key handoff, normal STK initiation, the payment ledger, and callback evidence.

The visual system uses a high-contrast obsidian operations rail, porcelain work surfaces, and a restrained Lynx green signal color. It deliberately prioritizes payment state, M-PESA receipts, callback handling, merchant shortcode/environment context, and the rule that STK acceptance is not proof of payment.

It is not yet a finished live-money security boundary. Browser bearer-token storage must be replaced with hardened server-managed sessions before production launch, and the console still requires real-device accessibility, cross-browser, load, and penetration testing.
