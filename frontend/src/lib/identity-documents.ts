import { DocumentCategory, IdentityDocumentType } from "@/lib/types";

/**
 * Structured document-type taxonomy for identity verification. Mirrors
 * backend/app/models/identity_verification.py exactly -- keep the two in sync.
 * Electricity/water/gas bills etc. are deliberately categorized as address /
 * residency evidence, never as government-issued identity documents.
 */

export const documentCategoryLabel: Record<DocumentCategory, string> = {
  identity: "Identity document",
  address: "Address / residency document",
  other: "Other document",
};

export const documentCategoryHint: Record<DocumentCategory, string> = {
  identity: "A government-issued document that proves who you are.",
  address: "A bill, statement or agreement that proves where you live.",
  other: "Anything else, such as a birth or marriage certificate.",
};

interface DocumentTypeOption {
  value: IdentityDocumentType;
  label: string;
}

export const documentTypesByCategory: Record<DocumentCategory, DocumentTypeOption[]> = {
  identity: [
    { value: "aadhaar", label: "Aadhaar" },
    { value: "pan_card", label: "PAN Card" },
    { value: "passport", label: "Passport" },
    { value: "driving_license", label: "Driving License" },
    { value: "voter_id", label: "Voter ID" },
    { value: "national_id", label: "National ID" },
    { value: "residence_permit", label: "Residence Permit" },
    { value: "permanent_resident_card", label: "Permanent Resident Card" },
    { value: "government_photo_id", label: "Government Photo ID" },
    { value: "government_employee_id", label: "Government Employee ID" },
  ],
  address: [
    { value: "electricity_bill", label: "Electricity Bill" },
    { value: "water_bill", label: "Water Bill" },
    { value: "gas_bill", label: "Gas Bill" },
    { value: "telephone_bill", label: "Telephone Bill" },
    { value: "internet_bill", label: "Internet Bill" },
    { value: "property_tax_bill", label: "Property Tax Bill" },
    { value: "bank_statement", label: "Bank Statement" },
    { value: "credit_card_statement", label: "Credit Card Statement" },
    { value: "government_address_certificate", label: "Government Address Certificate" },
    { value: "rental_agreement", label: "Rental Agreement" },
  ],
  other: [
    { value: "birth_certificate", label: "Birth Certificate" },
    { value: "marriage_certificate", label: "Marriage Certificate" },
    { value: "other_government_document", label: "Other Government Document" },
    { value: "other", label: "Other" },
  ],
};

export const documentCategories = Object.keys(documentTypesByCategory) as DocumentCategory[];

export const documentTypeLabel: Record<IdentityDocumentType, string> = Object.fromEntries(
  Object.values(documentTypesByCategory)
    .flat()
    .map((doc) => [doc.value, doc.label])
) as Record<IdentityDocumentType, string>;

export const documentTypeCategory: Record<IdentityDocumentType, DocumentCategory> = Object.fromEntries(
  (Object.entries(documentTypesByCategory) as [DocumentCategory, DocumentTypeOption[]][]).flatMap(([category, docs]) =>
    docs.map((doc) => [doc.value, category] as const)
  )
) as Record<IdentityDocumentType, DocumentCategory>;

export const ACCEPTED_DOCUMENT_EXTENSIONS = ".pdf,.jpg,.jpeg,.png";
export const ACCEPTED_DOCUMENT_MIME_TYPES = ["application/pdf", "image/jpeg", "image/png"];
export const MAX_DOCUMENT_SIZE_MB = 10;
