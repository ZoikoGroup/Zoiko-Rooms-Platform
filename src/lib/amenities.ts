/**
 * Curated amenity catalogue for listings. The backend stores amenities as a
 * plain list[str] with no fixed vocabulary (models/listing.py), so this list
 * is purely a frontend convenience -- picking one of these just appends its
 * `value` to that same string array. Hosts can still add anything not listed
 * here via the "Other" free-text input, so nothing is lost.
 */
export interface AmenityOption {
  value: string;
  label: string;
}

/** Furnishing is one choice, not several: ticking one of these un-ticks the others. */
export const FURNISHING_OPTIONS = ["Furnished", "Part Furnished", "Unfurnished"] as const;

export const COMMON_AMENITIES: AmenityOption[] = [
  { value: "Furnished", label: "Furnished" },
  { value: "Part Furnished", label: "Part Furnished" },
  { value: "Unfurnished", label: "Unfurnished" },
  { value: "Wi-Fi", label: "Wi-Fi" },
  { value: "Washing Machine", label: "Washing Machine" },
  { value: "Air Conditioning", label: "Air Conditioning" },
  { value: "Attached Bathroom", label: "Attached Bathroom" },
  { value: "Geyser / Hot Water", label: "Geyser / Hot Water" },
  { value: "Power Backup", label: "Power Backup" },
  { value: "Refrigerator", label: "Refrigerator" },
  { value: "Microwave", label: "Microwave" },
  { value: "TV", label: "TV" },
  { value: "Parking", label: "Parking" },
  { value: "Lift / Elevator", label: "Lift / Elevator" },
  { value: "Step-free Entrance", label: "Step-free Entrance" },
  { value: "CCTV Security", label: "CCTV Security" },
  { value: "Housekeeping", label: "Housekeeping" },
  { value: "Meals Included", label: "Meals Included" },
  { value: "Balcony", label: "Balcony" },
  { value: "Study Table", label: "Study Table" },
  { value: "Wardrobe", label: "Wardrobe" },
  { value: "Gym", label: "Gym" },
  { value: "Swimming Pool", label: "Swimming Pool" },
  { value: "Kitchen Access", label: "Kitchen Access" },
  // The words the Find a Room amenity filter uses (see FILTER_FEATURES below).
  { value: "Workspace", label: "Workspace" },
  { value: "Laundry", label: "Laundry" },
  { value: "Kitchen", label: "Kitchen" },
];

/** What renters can filter on in Find a Room, so the listing form asks for exactly these. `value` is what is
 *  saved in the listing's amenities; `wanted` is the filter's own name for it (lib/find-a-room-search.ts
 *  also recognises older wordings such as "Washing Machine", "Study Table" or "WiFi"). */
export const FILTER_FEATURES = [
  { label: "Wi-Fi", value: "Wi-Fi", wanted: "wi-fi" },
  { label: "Workspace / desk", value: "Workspace", wanted: "workspace" },
  { label: "Laundry / washing machine", value: "Laundry", wanted: "laundry" },
  { label: "Kitchen access", value: "Kitchen", wanted: "kitchen" },
] as const;

export const FILTER_ACCESSIBILITY = [
  { label: "Step-free entrance", value: "Step-free Entrance", filterName: "Step-free entrance" },
  { label: "Lift / elevator access", value: "Lift / Elevator", filterName: "Lift access" },
] as const;

/** Amenity values the dedicated filter sections handle, so the general amenities list doesn't repeat them. */
export const FILTER_HANDLED_AMENITIES: string[] = [
  ...FURNISHING_OPTIONS,
  "Wi-Fi", "Workspace", "Laundry", "Kitchen", "Washing Machine", "Study Table", "Kitchen Access",
  "Lift / Elevator", "Step-free Entrance", "Attached Bathroom",
];

export const COMMON_AMENITY_VALUES = new Set(COMMON_AMENITIES.map((a) => a.value));
