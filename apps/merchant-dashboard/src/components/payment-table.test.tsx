import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PaymentTable } from "./payment-table";

describe("PaymentTable", () => {
  it("renders M-PESA evidence and review state", () => {
    render(<PaymentTable payments={[{
      id: "pay_1", external_reference: "ORDER-100", amount: "100.00",
      customer_phone: "254712345678", status: "success", review_status: "needs_review",
      success_source: "status_query", receipt_status: "missing", created_at: "2026-07-16T08:00:00Z"
    }]} />);
    expect(screen.getByText("ORDER-100")).toBeInTheDocument();
    expect(screen.getByText(/status query · missing/)).toBeInTheDocument();
    expect(screen.getByText("needs review")).toBeInTheDocument();
  });
});
