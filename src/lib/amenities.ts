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

export const COMMON_AMENITIES: AmenityOption[] = [
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
  { value: "CCTV Security", label: "CCTV Security" },
  { value: "Housekeeping", label: "Housekeeping" },
  { value: "Meals Included", label: "Meals Included" },
  { value: "Balcony", label: "Balcony" },
  { value: "Study Table", label: "Study Table" },
  { value: "Wardrobe", label: "Wardrobe" },
  { value: "Gym", label: "Gym" },
  { value: "Swimming Pool", label: "Swimming Pool" },
  { value: "Kitchen Access", label: "Kitchen Access" },
];

export const COMMON_AMENITY_VALUES = new Set(COMMON_AMENITIES.map((a) => a.value));
