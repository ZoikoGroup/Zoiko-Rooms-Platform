/** ZR-PAY-LINK-003 Wireframe D: "Country / region" + "Account details
 *  [ secure jurisdiction-specific fields ]" -- mirrors
 *  backend/app/services/bank_field_schemas.py's registry exactly (same
 *  country list, same field keys/labels/patterns) so client-side
 *  validation matches what the server will actually accept. The server is
 *  still the authority -- this is only for immediate form feedback. */

export interface BankFieldDef {
  key: string;
  label: string;
  pattern: RegExp;
  hint: string;
}

export interface BankFieldSchema {
  fields: BankFieldDef[];
  primaryFieldKey: string;
}

const UK_SORT_CODE = /^\d{2}-?\d{2}-?\d{2}$/;
const UK_ACCOUNT_NUMBER = /^\d{8}$/;
const US_ROUTING_NUMBER = /^\d{9}$/;
const US_ACCOUNT_NUMBER = /^\d{4,17}$/;
const IBAN = /^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$/;
const GENERIC_IDENTIFIER = /^.{4,64}$/;

const GB_SCHEMA: BankFieldSchema = {
  fields: [
    { key: "sort_code", label: "Sort code", pattern: UK_SORT_CODE, hint: "6 digits, e.g. 12-34-56" },
    { key: "account_number", label: "Account number", pattern: UK_ACCOUNT_NUMBER, hint: "8 digits" },
  ],
  primaryFieldKey: "account_number",
};
const US_SCHEMA: BankFieldSchema = {
  fields: [
    { key: "routing_number", label: "Routing number", pattern: US_ROUTING_NUMBER, hint: "9 digits" },
    { key: "account_number", label: "Account number", pattern: US_ACCOUNT_NUMBER, hint: "4-17 digits" },
  ],
  primaryFieldKey: "account_number",
};
const IBAN_SCHEMA: BankFieldSchema = {
  fields: [{ key: "iban", label: "IBAN", pattern: IBAN, hint: "e.g. DE89370400440532013000" }],
  primaryFieldKey: "iban",
};
export const FALLBACK_SCHEMA: BankFieldSchema = {
  fields: [{ key: "account_identifier", label: "Account / payment ID", pattern: GENERIC_IDENTIFIER, hint: "At least 4 characters" }],
  primaryFieldKey: "account_identifier",
};

const IBAN_COUNTRIES = ["DE", "FR", "ES", "IT", "NL", "IE", "PT", "BE"] as const;

export const BANK_FIELD_SCHEMAS: Record<string, BankFieldSchema> = {
  GB: GB_SCHEMA,
  US: US_SCHEMA,
  ...Object.fromEntries(IBAN_COUNTRIES.map((code) => [code, IBAN_SCHEMA])),
};

/** Options for the "Country" select -- GB/US first (most common for this
 *  build's own markets), then the IBAN countries, then a generic "Other"
 *  that resolves to FALLBACK_SCHEMA (same fail-open, not fail-closed,
 *  posture as the backend registry -- an unlisted country must never
 *  block submission). */
export const COUNTRY_OPTIONS: { code: string; label: string }[] = [
  { code: "GB", label: "United Kingdom" },
  { code: "US", label: "United States" },
  { code: "DE", label: "Germany" },
  { code: "FR", label: "France" },
  { code: "ES", label: "Spain" },
  { code: "IT", label: "Italy" },
  { code: "NL", label: "Netherlands" },
  { code: "IE", label: "Ireland" },
  { code: "PT", label: "Portugal" },
  { code: "BE", label: "Belgium" },
  { code: "OTHER", label: "Other" },
];

/** Country-specific structured fields only ever make sense for
 *  BANK_TRANSFER -- a CASH/CARD/OTHER instruction has no bank routing
 *  details, so every other method always gets the generic single-field
 *  fallback regardless of country. Mirrors
 *  backend/app/services/bank_field_schemas.py:resolve_bank_field_schema's
 *  own method check exactly. */
export function resolveBankFieldSchema(countryCode: string, method: string = "BANK_TRANSFER"): BankFieldSchema {
  if (method !== "BANK_TRANSFER") return FALLBACK_SCHEMA;
  return BANK_FIELD_SCHEMAS[countryCode.toUpperCase()] ?? FALLBACK_SCHEMA;
}
