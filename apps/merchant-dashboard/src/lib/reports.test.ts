import { describe, expect, it } from "vitest";
import { paymentChannel, rowsToCsv } from "./reports";

describe("reports", () => {
  it("classifies walk-in, invoice and direct payments", () => {
    expect(paymentChannel({ external_reference: "WALKIN-20260722120000-ABC123" } as never)).toBe("Walk-in");
    expect(paymentChannel({ external_reference: "INV-000012-1" } as never)).toBe("Invoice");
    expect(paymentChannel({ external_reference: "ORDER-1" } as never)).toBe("Payment");
  });

  it("escapes exported CSV cells", () => {
    expect(rowsToCsv([{ reference: "A,1", description: "Line \"one\"", amount: 500 }])).toBe("reference,description,amount\n\"A,1\",\"Line \"\"one\"\"\",500");
  });
});
