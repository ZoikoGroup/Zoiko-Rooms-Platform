/** The room types a host picks from when listing a room. The Find a Room "Room Type" filter compares these
 *  exact words, so the form offers a fixed list instead of free text (which produced "private_room",
 *  "Private Room", "private room"... for the same thing). */
export const ROOM_TYPES = ["Private room", "En-suite", "Studio", "Single", "Double"] as const;

/** The list for a form field, keeping a value the listing already has (e.g. an older custom one) selectable. */
export function roomTypeChoices(current: string): string[] {
  const value = current.trim();
  const known = ROOM_TYPES as readonly string[];
  return value && !known.some((t) => t.toLowerCase() === value.toLowerCase()) ? [...known, value] : [...known];
}
