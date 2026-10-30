// @vitest-environment node
import { describe, it, expect } from "vitest";
import { toCsv } from "@/lib/csv-download";

describe("toCsv (ZR-87: exported payment sheet was empty)", () => {
  it("keeps every row when a cell contains a # (the old data: link cut the file there)", () => {
    const csv = toCsv(["Rental / Property", "Rental Code"], [
      ["Room #26", "#ZR-R-7"],
      ["Room #27", "#ZR-R-8"],
    ]);
    const lines = csv.split("\r\n");
    expect(lines).toHaveLength(3);
    expect(lines[1]).toBe('"Room #26","#ZR-R-7"');
    expect(lines[2]).toBe('"Room #27","#ZR-R-8"');
  });

  it("quotes commas and quotes inside text, and leaves numbers bare", () => {
    expect(toCsv(["a", "b"], [['He said "hi", ok', 350]])).toBe('"a","b"\r\n"He said ""hi"", ok",350');
  });

  it("keeps symbols like £ intact", () => {
    expect(toCsv(["c"], [["£350"]])).toContain("£350");
  });

  it("stops a text cell from running as a spreadsheet formula", () => {
    expect(toCsv(["n"], [["=HYPERLINK(\"x\")"], ["+1"], ["@SUM"]]).split("\r\n").slice(1)).toEqual([
      `"'=HYPERLINK(""x"")"`,
      `"'+1"`,
      `"'@SUM"`,
    ]);
  });

  it("writes empty cells for missing values", () => {
    expect(toCsv(["a", "b"], [[null, undefined]])).toBe('"a","b"\r\n,');
  });
});
