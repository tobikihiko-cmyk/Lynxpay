import { describe, expect, it } from "vitest";
import { apiErrorMessage } from "./errors";

describe("apiErrorMessage", () => {
  it("keeps plain backend error messages", () => {
    expect(apiErrorMessage({ detail: "Invalid email or password" }, "Fallback")).toBe(
      "Invalid email or password",
    );
  });

  it("formats FastAPI validation details without returning renderable objects", () => {
    expect(
      apiErrorMessage(
        {
          detail: [
            {
              type: "string_too_short",
              loc: ["body", "password"],
              msg: "String should have at least 1 character",
              input: "",
              ctx: { min_length: 1 },
            },
          ],
        },
        "Sign in failed",
      ),
    ).toBe("password: String should have at least 1 character");
  });

  it("falls back for unexpected error payloads", () => {
    expect(apiErrorMessage({ detail: { type: "unknown" } }, "Try again")).toBe("Try again");
  });
});
