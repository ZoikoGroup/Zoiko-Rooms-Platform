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
const IN_IFSC = /^[A-Za-z]{4}0[A-Za-z0-9]{6}$/;
const IN_ACCOUNT_NUMBER = /^\d{9,18}$/;
const UPI_ID = /^[A-Za-z0-9.\-_]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$/;
const PAYMENT_ALIAS = /^.{3,128}$/;

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
const IN_SCHEMA: BankFieldSchema = {
  fields: [
    { key: "ifsc", label: "IFSC", pattern: IN_IFSC, hint: "11 characters, e.g. HDFC0001234" },
    { key: "account_number", label: "Account number", pattern: IN_ACCOUNT_NUMBER, hint: "9-18 digits" },
  ],
  primaryFieldKey: "account_number",
};
export const UPI_SCHEMA: BankFieldSchema = {
  fields: [{ key: "upi_id", label: "UPI ID", pattern: UPI_ID, hint: "e.g. name@okhdfcbank" }],
  primaryFieldKey: "upi_id",
};
/** Instant rails other than UPI: a PayID, Pix key, PayNow ID, ... */
export const INSTANT_ALIAS_SCHEMA: BankFieldSchema = {
  fields: [{
    key: "payment_alias", label: "Payment ID / alias", pattern: PAYMENT_ALIAS,
    hint: "e.g. PayID, Pix key, PayNow ID, or the phone/email registered for instant payments",
  }],
  primaryFieldKey: "payment_alias",
};
export const WALLET_HANDLE_SCHEMA: BankFieldSchema = {
  fields: [{ key: "account_handle", label: "Wallet / app account", pattern: PAYMENT_ALIAS, hint: "The email, phone number or username to pay" }],
  primaryFieldKey: "account_handle",
};
export const MOBILE_MONEY_SCHEMA: BankFieldSchema = {
  fields: [{ key: "account_handle", label: "Mobile money number / account", pattern: PAYMENT_ALIAS, hint: "The number registered with the service" }],
  primaryFieldKey: "account_handle",
};
/** Cash is paid in person -- no account details; the host describes where
 *  and when in the instruction's notes. */
export const CASH_SCHEMA: BankFieldSchema = { fields: [], primaryFieldKey: "" };
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
  IN: IN_SCHEMA,
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
  { code: "IN", label: "India" },
  { code: "DE", label: "Germany" },
  { code: "FR", label: "France" },
  { code: "ES", label: "Spain" },
  { code: "IT", label: "Italy" },
  { code: "NL", label: "Netherlands" },
  { code: "IE", label: "Ireland" },
  { code: "PT", label: "Portugal" },
  { code: "BE", label: "Belgium" },
  { code: "AU", label: "Australia" },
  { code: "CA", label: "Canada" },
  { code: "BR", label: "Brazil" },
  { code: "SG", label: "Singapore" },
  { code: "MY", label: "Malaysia" },
  { code: "TH", label: "Thailand" },
  { code: "HK", label: "Hong Kong" },
  { code: "PH", label: "Philippines" },
  { code: "ID", label: "Indonesia" },
  { code: "KE", label: "Kenya" },
  { code: "GH", label: "Ghana" },
  { code: "UG", label: "Uganda" },
  { code: "OTHER", label: "Other" },
];

/** Country-specific structured fields only ever make sense for
 *  BANK_TRANSFER -- a CASH/CARD/OTHER instruction has no bank routing
 *  details, so every other method always gets the generic single-field
 *  fallback regardless of country. Mirrors
 *  backend/app/services/bank_field_schemas.py:resolve_bank_field_schema's
 *  own method check exactly. */
export function resolveBankFieldSchema(countryCode: string, method: string = "BANK_TRANSFER", subtype = ""): BankFieldSchema {
  // Canonical categories (ZR-PAY-METHODS-GLOBAL-001); a bare "UPI" is the legacy form.
  if (method === "UPI" || (method === "INSTANT_BANK_PAYMENT" && subtype === "UPI")) return UPI_SCHEMA;
  if (method === "INSTANT_BANK_PAYMENT") return INSTANT_ALIAS_SCHEMA;
  if (method === "DIGITAL_WALLET_P2P") return WALLET_HANDLE_SCHEMA;
  if (method === "MOBILE_MONEY") return MOBILE_MONEY_SCHEMA;
  if (method === "CASH") return CASH_SCHEMA;
  if (method !== "BANK_TRANSFER") return FALLBACK_SCHEMA;
  return BANK_FIELD_SCHEMAS[countryCode.toUpperCase()] ?? FALLBACK_SCHEMA;
}
