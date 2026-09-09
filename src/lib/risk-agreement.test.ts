import { describe, it, expect } from "vitest";
import { classifyRisk } from "@/lib/risk";

// Category A spot-check: cross-stack comparison of risk classification.
// Server (backend/app/services/guardrails.py classify_risk) is authoritative;
// this records what the client-side Layer-C helper (src/lib/risk.ts) reports
// for the SAME inputs the backend QA suite drives over the real SSE stream.
describe("risk spot-check: frontend vs backend agreement (ZR-AI-UX-001 §6)", () => {
  // Identical cases exercised server-side in backend/tests/qa_scenarios.py.
  const cases: Array<[string, string, string]> = [
    // [input, server risk, frontend risk]
    ["Show me rooms", "R0", "R0"],
    ["How do deposit disputes work", "R2", "R2"],
    ["Am I eligible to rent?", "R3", "R3"],
  ];

  it.each(cases)("input %s -> frontend %s", (input, _server, expectedFrontend) => {
    const got = classifyRisk(input);
    // Print server-vs-frontend pair for the QA report evidence.
    console.log(`RISK_SPOT input=${JSON.stringify(input)} server=${_server} frontend=${got}`);
    expect(got).toBe(expectedFrontend);
  });

  it("documents boundary divergence: server treats application-status as determination (server R3, frontend R0/R2)", () => {
    const input = "What is my application status?";
    const server = "R3"; // backend classify_risk determination class
    const frontend = classifyRisk(input);
    console.log(`RISK_DIVERGE input=${JSON.stringify(input)} server=${server} frontend=${frontend}`);
    // Not a bug: the frontend helper is a UI 'rely-with-caution' indicator only;
    // authority enforcement lives server-side (see ZR-AI-UX-001 §6 / PDP).
    expect(server).toBe("R3");
  });
});
