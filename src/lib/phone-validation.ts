export interface CountryDialCode {
  code: string;
  name: string;
  flag: string;
  minDigits: number;
  maxDigits: number;
  example: string;
}

export const SUPPORTED_COUNTRIES: CountryDialCode[] = [
  { code: "+91", name: "India", flag: "🇮🇳", minDigits: 10, maxDigits: 10, example: "98200 11223" },
  { code: "+44", name: "United Kingdom", flag: "🇬🇧", minDigits: 10, maxDigits: 11, example: "7911 123456" },
  { code: "+1", name: "United States / Canada", flag: "🇺🇸", minDigits: 10, maxDigits: 10, example: "415 555 2671" },
  { code: "+971", name: "United Arab Emirates", flag: "🇦🇪", minDigits: 9, maxDigits: 9, example: "50 123 4567" },
  { code: "+61", name: "Australia", flag: "🇦🇺", minDigits: 9, maxDigits: 9, example: "412 345 678" },
  { code: "+65", name: "Singapore", flag: "🇸🇬", minDigits: 8, maxDigits: 8, example: "8123 4567" },
  { code: "+49", name: "Germany", flag: "🇩🇪", minDigits: 10, maxDigits: 11, example: "151 23456789" },
  { code: "+33", name: "France", flag: "🇫🇷", minDigits: 9, maxDigits: 9, example: "6 12 34 56 78" },
  { code: "+353", name: "Ireland", flag: "🇮🇪", minDigits: 9, maxDigits: 9, example: "85 123 4567" },
  { code: "+64", name: "New Zealand", flag: "🇳🇿", minDigits: 8, maxDigits: 10, example: "21 123 4567" },
  { code: "+81", name: "Japan", flag: "🇯🇵", minDigits: 10, maxDigits: 10, example: "90 1234 5678" },
  { code: "+82", name: "South Korea", flag: "🇰🇷", minDigits: 9, maxDigits: 10, example: "10 1234 5678" },
  { code: "+60", name: "Malaysia", flag: "🇲🇾", minDigits: 9, maxDigits: 10, example: "12 345 6789" },
  { code: "+966", name: "Saudi Arabia", flag: "🇸🇦", minDigits: 9, maxDigits: 9, example: "50 123 4567" },
  { code: "+974", name: "Qatar", flag: "🇶🇦", minDigits: 8, maxDigits: 8, example: "3312 3456" },
  { code: "+27", name: "South Africa", flag: "🇿🇦", minDigits: 9, maxDigits: 9, example: "71 123 4567" },
];

/**
 * Validates a phone number based on country code and subscriber digits.
 * Returns null if valid, or an error message string if invalid.
 */
export function validatePhoneWithCountry(countryCode: string, digits: string): string | null {
  const trimmedDigits = digits.trim().replace(/\D/g, "");
  if (!trimmedDigits) {
    return "Phone number is required.";
  }

  const country = SUPPORTED_COUNTRIES.find((c) => c.code === countryCode);
  if (country) {
    if (country.minDigits === country.maxDigits) {
      if (trimmedDigits.length !== country.minDigits) {
        return `Phone number for ${country.name} (${country.code}) must be exactly ${country.minDigits} digits (entered ${trimmedDigits.length}).`;
      }
    } else {
      if (trimmedDigits.length < country.minDigits || trimmedDigits.length > country.maxDigits) {
        return `Phone number for ${country.name} (${country.code}) must be between ${country.minDigits} and ${country.maxDigits} digits (entered ${trimmedDigits.length}).`;
      }
    }
  } else {
    if (!/^\+[0-9]{1,4}$/.test(countryCode)) {
      return "Please select or enter a valid country code (e.g. +91).";
    }
    if (trimmedDigits.length < 7 || trimmedDigits.length > 15) {
      return "Phone number must be between 7 and 15 digits.";
    }
  }

  return null;
}

/**
 * Splits a full stored phone string (e.g. "+91 98200 11223" or "9820011223")
 * into country code and subscriber digits.
 */
export function splitPhoneNumber(fullPhone?: string | null): { countryCode: string; digits: string } {
  if (!fullPhone) {
    return { countryCode: "+91", digits: "" };
  }
  const trimmed = fullPhone.trim();

  // Find longest matching country code
  const match = SUPPORTED_COUNTRIES
    .filter((c) => trimmed.startsWith(c.code))
    .sort((a, b) => b.code.length - a.code.length)[0];

  if (match) {
    const digitsPart = trimmed.slice(match.code.length).trim().replace(/\D/g, "");
    return { countryCode: match.code, digits: digitsPart };
  }

  // If starts with +, match prefix +1 to +9999
  if (trimmed.startsWith("+")) {
    const parts = trimmed.match(/^(\+\d{1,4})\s*(.*)$/);
    if (parts) {
      return { countryCode: parts[1], digits: parts[2].replace(/\D/g, "") };
    }
  }

  // Default to +91 if no country code was stored
  return { countryCode: "+91", digits: trimmed.replace(/\D/g, "") };
}
