/** Builds CSV text that opens correctly in Excel, Sheets and Numbers. */
export function toCsv(headers: string[], rows: (string | number | null | undefined)[][]): string {
  const cell = (value: string | number | null | undefined): string => {
    if (value === null || value === undefined) return "";
    if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
    // A text cell that starts with = + - @ would be run as a formula by Excel; a leading ' keeps it text.
    const text = /^[=+\-@\t\r]/.test(value) ? `'${value}` : value;
    return `"${text.replace(/"/g, '""')}"`;
  };
  return [headers.map((h) => cell(h)).join(","), ...rows.map((row) => row.map(cell).join(","))].join("\r\n");
}

/** Downloads rows as a .csv file. Uses a real file blob rather than a data: link --
 *  a data: link silently cuts the file at the first "#" (e.g. "Rental #ZR-R-7"),
 *  which left the exported sheet empty. The BOM makes Excel read £, € and accents correctly. */
export function downloadCsv(
  filename: string,
  headers: string[],
  rows: (string | number | null | undefined)[][],
): void {
  const blob = new Blob(["﻿", toCsv(headers, rows)], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
