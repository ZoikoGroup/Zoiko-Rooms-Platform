/**
 * ZR-SUBLET-PAY-003 -- sublet payments made directly by bank transfer, UPI or
 * cash. Zoiko Rooms never receives, holds or forwards this money and charges
 * no fee on it (Listing Fee only). Payee, deposit route, accepted methods and
 * every readiness state come from the server.
 */

import { ApiError, apiClientFetch } from "@/lib/api-client";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type DirectPaymentMethod = "BANK_TRANSFER" | "UPI" | "CASH";
export type RentPayeeType = "LANDLORD_AGENT" | "SUBLESSOR";
export type DepositRoute =
  | "NOT_REQUIRED" | "PAY_TO_LANDLORD_OR_AGENT" | "PAY_TO_SUBLESSOR" | "PAY_TO_CUSTODIAN" | "PROHIBITED_OR_UNRESOLVED";
export type ReadinessState = "READY" | "SETUP_INCOMPLETE" | "ACTION_REQUIRED" | "BLOCKED" | "NOT_REQUIRED";
export type SubletRole = "SUBLESSOR" | "SUBTENANT" | "LANDLORD";

export interface Readiness {
  state: ReadinessState;
  reason_codes: string[];
  message: string;
}

export interface SubletPaymentArrangementView {
  subletRequestId: number;
  role: SubletRole;
  subletStatus: string;
  arrangementType: string;
  arrangement: null | {
    id: number;
    versionNo: number;
    version: number;
    locked: boolean;
    lockedAt: string | null;
    rentPayee: { type: RentPayeeType; name: string; basis: string; why: string };
    deposit: {
      route: DepositRoute; payeeName: string; custodianName: string; custodianReference: string;
      custodianInstructions: string; custodianHint: string; sublessorAllowed: boolean; packRoute: DepositRoute;
    };
    acceptedMethods: DirectPaymentMethod[];
    terms: { rentAmount: number; depositAmount: number; currency: string; rentDueDay: number | null;
      firstPaymentDate: string | null; platformFee: number };
    amendmentReason: string;
    readiness: { rent: Readiness; deposit: Readiness; reason_codes: string[] };
  };
  eligiblePayees: { type: RentPayeeType; name: string; basis: string; why: string }[];
  canEdit: boolean;
}

export interface ArrangementChange {
  rentPayeeType?: RentPayeeType;
  acceptedMethods?: DirectPaymentMethod[];
  depositRoute?: DepositRoute;
  custodianName?: string;
  custodianReference?: string;
  custodianInstructions?: string;
  amendmentReason?: string;
}

export interface SubletTransactionItem {
  kind: "OBLIGATION" | "PAYMENT" | "RETURN";
  at: string;
  obligationId: number;
  recordId?: number;
  returnId?: number;
  purpose: string;
  amount: number;
  currency: string;
  status: string;
  dueDate?: string;
  paidOn?: string;
  confirmedAt?: string | null;
  method?: string;
  payeeType?: string;
  payeeName?: string;
  reference?: string;
  externalReference?: string;
  evidence: string;
}

export interface SubletTransactions {
  subletRequestId: number;
  role: SubletRole;
  items: SubletTransactionItem[];
  totals: { due: number; paid: number };
  currency: string;
}

export interface HowToPay {
  obligationId: number;
  purpose: string;
  label: string;
  amount: number;
  outstanding: number;
  currency: string;
  dueDate: string;
  periodStart: string | null;
  periodEnd: string | null;
  platformFee: number;
  total: number;
  payee: { name: string; type: string; why: string };
  acceptedMethods: DirectPaymentMethod[];
  paymentReference: string;
  method: string;
  countryCode: string;
  /** The payee's bank account fields or UPI ID -- only once the agreement is
   *  accepted and payment isn't blocked. */
  details: Record<string, string> | null;
  custodian: { name: string; reference: string; instructions: string } | null;
  additionalInstructions: string;
  blockedReason: string;
  setupIncomplete: boolean;
  zoikoNotice: string;
}

export const methodLabel: Record<DirectPaymentMethod, string> = {
  BANK_TRANSFER: "Bank transfer", UPI: "UPI", CASH: "Cash",
};

export const depositRouteLabel: Record<DepositRoute, string> = {
  NOT_REQUIRED: "No deposit",
  PAY_TO_LANDLORD_OR_AGENT: "Paid to the landlord / agent",
  PAY_TO_SUBLESSOR: "Paid to the original tenant",
  PAY_TO_CUSTODIAN: "Paid to a deposit protection scheme",
  PROHIBITED_OR_UNRESOLVED: "Not settled yet",
};

export const payeeTypeLabel: Record<string, string> = {
  LANDLORD_AGENT: "Landlord / authorized agent",
  SUBLESSOR: "Original tenant (sublessor)",
  CUSTODIAN: "Deposit protection scheme",
};

export function getSubletPaymentArrangement(subletId: number) {
  return apiClientFetch<SubletPaymentArrangementView>(`/api/users/sublets/${subletId}/payment-arrangement`);
}

export function updateSubletPaymentArrangement(subletId: number, version: number, change: ArrangementChange) {
  return apiClientFetch<SubletPaymentArrangementView>(`/api/users/sublets/${subletId}/payment-arrangement`, {
    method: "POST", headers: { "If-Match": String(version) }, body: JSON.stringify(change),
  });
}

export function getSubletTransactions(subletId: number) {
  return apiClientFetch<SubletTransactions>(`/api/users/sublets/${subletId}/transactions`);
}

export function getHowToPay(obligationId: number) {
  return apiClientFetch<HowToPay>(`/api/users/rental-payments/obligations/${obligationId}/how-to-pay`);
}

/** Receipt PDF for a payment its payee confirmed. */
export async function downloadRentalPaymentReceipt(recordId: number): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/users/rental-payments/records/${recordId}/receipt`, { credentials: "include" });
  if (!res.ok) throw new ApiError(res.status, "A receipt is available once the payee confirms the payment.");
  return res.blob();
}
