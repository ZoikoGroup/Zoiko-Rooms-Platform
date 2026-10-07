/**
 * DIGIPIN -- India Post's national digital address grid (Department of Posts,
 * open algorithm). A 10-character code such as "4P3-JK8-52C9" names a cell of
 * roughly 4 m x 4 m anywhere in India, so a host whose door number isn't on
 * Google's map can still place the property marker exactly. Hosts get their
 * DIGIPIN from India Post's "Know Your DIGIPIN" site / app.
 */

const GRID = [
  ["F", "C", "9", "8"],
  ["J", "3", "2", "7"],
  ["K", "4", "5", "6"],
  ["L", "M", "P", "T"],
];
const BOUNDS = { minLat: 2.5, maxLat: 38.5, minLon: 63.5, maxLon: 99.5 };

/** "4p3jk852c9", "4P3-JK8-52C9" -> "4P3JK852C9", or null when it isn't a DIGIPIN. */
export function normalizeDigipin(input: string): string | null {
  const code = input.toUpperCase().replace(/[\s-]/g, "");
  if (code.length !== 10) return null;
  return [...code].every((c) => GRID.some((row) => row.includes(c))) ? code : null;
}

/** Centre of the DIGIPIN cell. */
export function decodeDigipin(input: string): { latitude: number; longitude: number } | null {
  const code = normalizeDigipin(input);
  if (!code) return null;
  let { minLat, maxLat, minLon, maxLon } = BOUNDS;
  for (const char of code) {
    const row = GRID.findIndex((r) => r.includes(char));
    const col = GRID[row].indexOf(char);
    const latDiv = (maxLat - minLat) / 4;
    const lonDiv = (maxLon - minLon) / 4;
    const top = maxLat - latDiv * row;
    maxLat = top;
    minLat = top - latDiv;
    minLon = minLon + lonDiv * col;
    maxLon = minLon + lonDiv;
  }
  return { latitude: (minLat + maxLat) / 2, longitude: (minLon + maxLon) / 2 };
}

/** The DIGIPIN for a point in India (used to show the host their code). */
export function encodeDigipin(latitude: number, longitude: number): string | null {
  let { minLat, maxLat, minLon, maxLon } = BOUNDS;
  if (latitude < minLat || latitude > maxLat || longitude < minLon || longitude > maxLon) return null;
  let code = "";
  for (let level = 1; level <= 10; level++) {
    const latDiv = (maxLat - minLat) / 4;
    const lonDiv = (maxLon - minLon) / 4;
    const row = Math.min(3, Math.max(0, 3 - Math.floor((latitude - minLat) / latDiv)));
    const col = Math.min(3, Math.max(0, Math.floor((longitude - minLon) / lonDiv)));
    code += GRID[row][col];
    if (level === 3 || level === 6) code += "-";
    maxLat = minLat + latDiv * (4 - row);
    minLat = minLat + latDiv * (3 - row);
    minLon = minLon + lonDiv * col;
    maxLon = minLon + lonDiv;
  }
  return code;
}
