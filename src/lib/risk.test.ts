import { describe, it, expect } from "vitest";
import { classifyRisk, riskTopicName } from "@/lib/risk";

describe("classifyRisk (client-side rely warning only)", () => {
  it("returns R0 for safe informational queries", () => {
    expect(classifyRisk("show me rooms in Mumbai")).toBe("R0");
    expect(classifyRisk("")).toBe("R0");
  });

  it("escalates determination/eligibility wording to R3", () => {
    expect(classifyRisk("Am I eligible for the right-to-rent scheme?")).toBe("R3");
    expect(classifyRisk("should I be approved for this application")).toBe("R3");
  });

  it("escalates safety crises to R4", () => {
    expect(classifyRisk("I'm in danger right now")).toBe("R4");
  });

  it("flags high-consequence topics as R2", () => {
    expect(classifyRisk("how do deposit disputes work")).toBe("R2");
    expect(classifyRisk("what is eviction")).toBe("R2");
  });
});

describe("riskTopicName", () => {
  it("names the matching high-consequence topic", () => {
    expect(riskTopicName("deposit protection rules")).toBe("deposit");
    expect(riskTopicName("immigration and right to rent")).toBe("right-to-rent");
    expect(riskTopicName("nothing risky here")).toBe("");
  });
});
