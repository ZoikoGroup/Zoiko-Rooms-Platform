"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Building2, CalendarClock, ClipboardList, CreditCard, DoorOpen, Download, Repeat, Search, TrendingUp } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { ApiError } from "@/lib/api-client";
import { DisputeCasesSection } from "@/components/user/DisputeCasePanel";
import {
  AutopayMandate,
  BookingChangeRequest,
  ConditionRating,
  ConditionReportItem,
  ConditionReportType,
  DisputeCaseRead,
  PaymentPreview,
  PublicListing,
  RefundEntitlement,
  SubletArrangementType,
  SubletRequest,
  SubletRenterLookup,
  TerminationCase,
  TerminationCasePreview,
  UserOccupancy,
} from "@/lib/types";
import {
  bookingChangeRequestStatusTone,
  bookingChangeTypeLabel,
  CO_TENANCY_ARRANGEMENT_TYPES,
  occupancyStatusTone,
  refundEntitlementLineItemLabel,
  RENTER_TERMINATION_CAUSE_CODES,
  subletArrangementTypeLabel,
  terminationCauseCodeLabel,
  terminationCaseStatusTone,
} from "@/lib/status";
import { addMonths, formatCurrency, formatDate } from "@/lib/utils";
import {
  acceptAlternativeChangeTerms,
  acceptOwnMutualSurrender,
  addOwnConditionReportItem,
  cancelOwnBookingBeforeMoveIn,
  confirmHandoverReceipt,
  createAutopayMandate,
  declineAlternativeChangeTerms,
  confirmMoveOutReady,
  declineOwnMutualSurrender,
  errorMessage,
  getObligationAvailableMethods,
  giveMoveOutNotice,
  getOwnAgreementPaymentPreview,
  getOwnRefundEntitlement,
  listMyAutopayMandates,
  listMyChangeRequests,
  listOccupancies,
  listOwnConditionReport,
  listOwnTerminationCases,
  listPublicListings,
  listSubletRequests,
  renterDisputes,
  lookupSubletRenter,
  getOwnSubletTerminology,
  payOwnObligation,
  previewOwnTermination,
  requestOwnTermination,
  revokeAutopayMandate,
  submitDepositChangeRequest,
  submitExtensionRequest,
  submitFinancialChangeRequest,
  submitPremisesChangeRequest,
  submitSubletRequest,
  tenantAgreementPdfUrl,
  withdrawChangeRequest,
  withdrawOwnTerminationCase,
} from "@/lib/user-api";
import { Card, EmptyState, Field, Toast, inputClass, useToast } from "@/components/user/ui";
import { RentalTransactionRecord } from "@/components/user/RentalTransactionRecord";

const PAYMENT_METHOD_LABELS: Record<string, string> = {
  CARD: "Card",
  BANK_DEBIT: "Bank debit",
  BANK_TRANSFER: "Bank transfer",
  PAY_BY_BANK: "Pay by bank",
  DIGITAL_WALLET: "Digital wallet",
  LOCAL_REAL_TIME: "UPI / instant transfer",
  EXTERNAL: "Cash / cheque (recorded by host)",
};

export function RentalsManager() {
  const router = useRouter();
  const { toast, showToast } = useToast();
  const [occupancies, setOccupancies] = useState<UserOccupancy[]>([]);
  const [changeRequests, setChangeRequests] = useState<BookingChangeRequest[]>([]);
  const [loading, setLoading] = useState(true);

  const [subletFor, setSubletFor] = useState<UserOccupancy | null>(null);
  const [subletStep, setSubletStep] = useState<1 | 2 | 3>(1);
  const [proposedEmail, setProposedEmail] = useState("");
  const [lookupResult, setLookupResult] = useState<SubletRenterLookup | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const [evidenceRef, setEvidenceRef] = useState("");
  const [subletReason, setSubletReason] = useState("");
  const [arrangementType, setArrangementType] = useState<SubletArrangementType>("ASSIGNMENT_FULL");
  const [proposedMonthlyRent, setProposedMonthlyRent] = useState("");
  const [subletStartDate, setSubletStartDate] = useState("");
  const [subletEndDate, setSubletEndDate] = useState("");
  const [confirmNoPermissionYet, setConfirmNoPermissionYet] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [duplicateSubletRequest, setDuplicateSubletRequest] = useState<SubletRequest | null>(null);
  const [subletUiTerm, setSubletUiTerm] = useState("sublet");

  const [extensionFor, setExtensionFor] = useState<UserOccupancy | null>(null);
  const [additionalMonths, setAdditionalMonths] = useState("1");
  const [extensionReason, setExtensionReason] = useState("");
  const [extensionSubmitting, setExtensionSubmitting] = useState(false);
  const [extensionError, setExtensionError] = useState("");
  const [withdrawingId, setWithdrawingId] = useState<number | null>(null);
  const [receiptConfirmedIds, setReceiptConfirmedIds] = useState<Set<number>>(new Set());
  const [confirmingReceiptId, setConfirmingReceiptId] = useState<number | null>(null);

  // Section 9 gap: the move-out mirror of the move-in receipt state above.
  const [moveOutNoticeIds, setMoveOutNoticeIds] = useState<Set<number>>(new Set());
  const [moveOutReadyIds, setMoveOutReadyIds] = useState<Set<number>>(new Set());
  const [moveOutBusyId, setMoveOutBusyId] = useState<number | null>(null);

  // Section 9 gap: move-in/move-out condition report (photos + notes).
  const [conditionFor, setConditionFor] = useState<UserOccupancy | null>(null);
  const [conditionItems, setConditionItems] = useState<ConditionReportItem[]>([]);
  const [conditionReportType, setConditionReportType] = useState<ConditionReportType>("MOVE_OUT");
  const [conditionArea, setConditionArea] = useState("");
  const [conditionRating, setConditionRating] = useState<ConditionRating | "">("");
  const [conditionNotes, setConditionNotes] = useState("");
  const [conditionFile, setConditionFile] = useState<File | null>(null);
  const [conditionSubmitting, setConditionSubmitting] = useState(false);
  const [conditionError, setConditionError] = useState("");

  // Section 7 gap: cancelling a signed-but-not-moved-in booking previously
  // had no renter-facing entry point at all.
  const [cancelFor, setCancelFor] = useState<UserOccupancy | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelSubmitting, setCancelSubmitting] = useState(false);
  const [cancelError, setCancelError] = useState("");

  // Section 6 gap: the whole termination/refund-entitlement flow previously
  // had zero renter-facing UI despite a complete backend.
  const [terminationCases, setTerminationCases] = useState<Record<number, TerminationCase>>({});
  const [refundEntitlements, setRefundEntitlements] = useState<Record<number, RefundEntitlement>>({});
  const [terminationFor, setTerminationFor] = useState<UserOccupancy | null>(null);
  const [terminationCauseCode, setTerminationCauseCode] = useState<string>(RENTER_TERMINATION_CAUSE_CODES[0]);
  const [terminationNotes, setTerminationNotes] = useState("");
  const [terminationPreview, setTerminationPreview] = useState<TerminationCasePreview | null>(null);
  const [terminationPreviewing, setTerminationPreviewing] = useState(false);
  const [terminationSubmitting, setTerminationSubmitting] = useState(false);
  const [terminationError, setTerminationError] = useState("");
  const [terminationActionBusy, setTerminationActionBusy] = useState<number | null>(null);

  // ZR-ENG-CLR-010 gap: the general-purpose Dispute Resolution engine had a
  // complete backend but zero renter-facing UI. `renterDisputes.listCases()`
  // is guest-scoped (all of this renter's cases, not filtered by occupancy),
  // so it's loaded once here and grouped per occupancy for display.
  const [disputeCases, setDisputeCases] = useState<DisputeCaseRead[]>([]);

  const [premisesFor, setPremisesFor] = useState<UserOccupancy | null>(null);
  const [availableListings, setAvailableListings] = useState<PublicListing[]>([]);
  const [listingsLoading, setListingsLoading] = useState(false);
  const [targetListingId, setTargetListingId] = useState("");
  const [premisesReason, setPremisesReason] = useState("");
  const [premisesSubmitting, setPremisesSubmitting] = useState(false);
  const [premisesError, setPremisesError] = useState("");

  const [financialFor, setFinancialFor] = useState<UserOccupancy | null>(null);
  const [financialProposedRent, setFinancialProposedRent] = useState("");
  const [financialProposedDeposit, setFinancialProposedDeposit] = useState("");
  const [financialReason, setFinancialReason] = useState("");
  const [financialSubmitting, setFinancialSubmitting] = useState(false);
  const [financialError, setFinancialError] = useState("");

  const [depositFor, setDepositFor] = useState<UserOccupancy | null>(null);
  const [depositProposedAmount, setDepositProposedAmount] = useState("");
  const [depositReason, setDepositReason] = useState("");
  const [depositSubmitting, setDepositSubmitting] = useState(false);
  const [depositError, setDepositError] = useState("");

  // Section 5 gap: renters previously had no way to pay rent themselves
  // after move-in, or to opt into autopay -- both keyed by agreementId/
  // occupancyId since a renter can have more than one active occupancy.
  const [paymentPreviews, setPaymentPreviews] = useState<Record<number, PaymentPreview>>({});
  const [obligationMethods, setObligationMethods] = useState<Record<number, string[]>>({});
  const [selectedMethod, setSelectedMethod] = useState<Record<number, string>>({});
  const [payingObligationId, setPayingObligationId] = useState<number | null>(null);
  const [autopayMandates, setAutopayMandates] = useState<AutopayMandate[]>([]);
  const [autopayBusyOccupancyId, setAutopayBusyOccupancyId] = useState<number | null>(null);
  const [recordFor, setRecordFor] = useState<UserOccupancy | null>(null);

  const load = useCallback(async () => {
    try {
      const [occupanciesData, changeRequestsData, mandatesData] = await Promise.all([
        listOccupancies(), listMyChangeRequests(), listMyAutopayMandates().catch(() => []),
      ]);
      setOccupancies(occupanciesData);
      setChangeRequests(changeRequestsData);
      setAutopayMandates(mandatesData);

      renterDisputes.listCases().then(setDisputeCases).catch(() => {});

      const active = occupanciesData.filter((o) => o.status === "ACTIVE" && o.agreementId);
      await Promise.all(
        active.map(async (occupancy) => {
          const agreementId = occupancy.agreementId!;
          try {
            const preview = await getOwnAgreementPaymentPreview(agreementId);
            setPaymentPreviews((prev) => ({ ...prev, [agreementId]: preview }));
            preview.amountDueNow.forEach((o) => {
              if (o.status === "PAID") return;
              getObligationAvailableMethods(o.id)
                .then(({ methodClasses }) => {
                  setObligationMethods((prev) => ({ ...prev, [o.id]: methodClasses }));
                  setSelectedMethod((prev) => ({ ...prev, [o.id]: prev[o.id] ?? methodClasses[0] }));
                })
                .catch(() => {});
            });
          } catch {
            // No payment preview for this agreement -- nothing due, or not reachable; leave it unset.
          }

          try {
            const cases = await listOwnTerminationCases(occupancy.id);
            const openCase = cases.find((c) => !["TERMINATED", "WITHDRAWN", "REJECTED_PATHWAY"].includes(c.status)) ?? cases[0];
            if (openCase) {
              setTerminationCases((prev) => ({ ...prev, [occupancy.id]: openCase }));
              if (openCase.status === "TERMINATED" || openCase.status === "EFFECTIVE_DATE_SET") {
                getOwnRefundEntitlement(openCase.id)
                  .then((entitlement) => setRefundEntitlements((prev) => ({ ...prev, [openCase.id]: entitlement })))
                  .catch(() => {});
              }
            }
          } catch {
            // No termination case for this occupancy -- nothing to show.
          }
        }),
      );
    } catch (err) {
      showToast(errorMessage(err, "Could not load your rentals."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleConfirmReceipt(occupancy: UserOccupancy) {
    setConfirmingReceiptId(occupancy.id);
    try {
      await confirmHandoverReceipt(occupancy.id);
      setReceiptConfirmedIds((prev) => new Set(prev).add(occupancy.id));
      showToast("Receipt confirmed — your host can now complete move-in.");
    } catch (err) {
      showToast(errorMessage(err, "Could not confirm receipt."), "error");
    } finally {
      setConfirmingReceiptId(null);
    }
  }

  async function handleGiveMoveOutNotice(occupancy: UserOccupancy) {
    setMoveOutBusyId(occupancy.id);
    try {
      await giveMoveOutNotice(occupancy.id, "");
      setMoveOutNoticeIds((prev) => new Set(prev).add(occupancy.id));
      showToast("Move-out notice given — your host has been notified.");
    } catch (err) {
      showToast(errorMessage(err, "Could not give move-out notice."), "error");
    } finally {
      setMoveOutBusyId(null);
    }
  }

  async function handleConfirmMoveOutReady(occupancy: UserOccupancy) {
    setMoveOutBusyId(occupancy.id);
    try {
      await confirmMoveOutReady(occupancy.id, "");
      setMoveOutReadyIds((prev) => new Set(prev).add(occupancy.id));
      showToast("Marked as moved out — your host can now confirm and close out the tenancy.");
    } catch (err) {
      showToast(errorMessage(err, "Could not confirm move-out."), "error");
    } finally {
      setMoveOutBusyId(null);
    }
  }

  async function openCondition(occupancy: UserOccupancy) {
    setConditionFor(occupancy);
    setConditionReportType("MOVE_OUT");
    setConditionArea("");
    setConditionRating("");
    setConditionNotes("");
    setConditionFile(null);
    setConditionError("");
    try {
      setConditionItems(await listOwnConditionReport(occupancy.id));
    } catch {
      setConditionItems([]);
    }
  }

  async function handleAddConditionItem() {
    if (!conditionFor) return;
    setConditionSubmitting(true);
    setConditionError("");
    try {
      await addOwnConditionReportItem(conditionFor.id, {
        reportType: conditionReportType, area: conditionArea.trim(),
        conditionRating: conditionRating || undefined, notes: conditionNotes.trim(), file: conditionFile,
      });
      setConditionArea("");
      setConditionRating("");
      setConditionNotes("");
      setConditionFile(null);
      setConditionItems(await listOwnConditionReport(conditionFor.id));
      showToast("Condition report item added.");
    } catch (err) {
      setConditionError(errorMessage(err, "Could not add this item."));
    } finally {
      setConditionSubmitting(false);
    }
  }

  function openCancel(occupancy: UserOccupancy) {
    setCancelFor(occupancy);
    setCancelReason("");
    setCancelError("");
  }

  async function handleCancelBeforeMoveIn() {
    if (!cancelFor) return;
    setCancelSubmitting(true);
    setCancelError("");
    try {
      const result = await cancelOwnBookingBeforeMoveIn(cancelFor.id, cancelReason.trim());
      setCancelFor(null);
      showToast(
        result.feeAmount > 0
          ? `Booking cancelled. ${formatCurrency(result.refundedAmount, cancelFor.currency)} refunded (a ${formatCurrency(result.feeAmount, cancelFor.currency)} cancellation fee applied).`
          : `Booking cancelled. ${formatCurrency(result.refundedAmount, cancelFor.currency)} refunded in full.`,
      );
      await load();
    } catch (err) {
      setCancelError(errorMessage(err, "Could not cancel this booking."));
    } finally {
      setCancelSubmitting(false);
    }
  }

  function openTermination(occupancy: UserOccupancy) {
    setTerminationFor(occupancy);
    setTerminationCauseCode(RENTER_TERMINATION_CAUSE_CODES[0]);
    setTerminationNotes("");
    setTerminationPreview(null);
    setTerminationError("");
  }

  async function handlePreviewTermination() {
    if (!terminationFor) return;
    setTerminationPreviewing(true);
    setTerminationError("");
    try {
      const preview = await previewOwnTermination(terminationFor.id, { causeCode: terminationCauseCode });
      setTerminationPreview(preview);
    } catch (err) {
      setTerminationError(errorMessage(err, "Could not preview this."));
    } finally {
      setTerminationPreviewing(false);
    }
  }

  async function handleSubmitTermination() {
    if (!terminationFor) return;
    setTerminationSubmitting(true);
    setTerminationError("");
    try {
      const created = await requestOwnTermination(terminationFor.id, {
        causeCode: terminationCauseCode, notes: terminationNotes.trim(),
      });
      setTerminationCases((prev) => ({ ...prev, [terminationFor.id]: created }));
      setTerminationFor(null);
      showToast("Your request to end your stay has been submitted.");
      await load();
    } catch (err) {
      setTerminationError(errorMessage(err, "Could not submit this request."));
    } finally {
      setTerminationSubmitting(false);
    }
  }

  async function handleWithdrawTermination(occupancyId: number, caseId: number) {
    setTerminationActionBusy(caseId);
    try {
      await withdrawOwnTerminationCase(caseId);
      setTerminationCases((prev) => {
        const next = { ...prev };
        delete next[occupancyId];
        return next;
      });
      showToast("Request withdrawn.");
    } catch (err) {
      showToast(errorMessage(err, "Could not withdraw this request."), "error");
    } finally {
      setTerminationActionBusy(null);
    }
  }

  async function handleSurrenderResponse(caseId: number, accept: boolean) {
    setTerminationActionBusy(caseId);
    try {
      const updated = accept ? await acceptOwnMutualSurrender(caseId) : await declineOwnMutualSurrender(caseId);
      setTerminationCases((prev) => {
        const occupancyId = Object.keys(prev).find((key) => prev[Number(key)].id === caseId);
        if (!occupancyId) return prev;
        return { ...prev, [Number(occupancyId)]: updated };
      });
      showToast(accept ? "Surrender accepted." : "Surrender declined.");
    } catch (err) {
      showToast(errorMessage(err, "Could not respond to this."), "error");
    } finally {
      setTerminationActionBusy(null);
    }
  }

  async function handlePayObligation(obligationId: number, agreementId: number) {
    setPayingObligationId(obligationId);
    try {
      await payOwnObligation(obligationId, selectedMethod[obligationId] ?? "CARD");
      showToast("Payment successful.");
      const refreshed = await getOwnAgreementPaymentPreview(agreementId);
      setPaymentPreviews((prev) => ({ ...prev, [agreementId]: refreshed }));
    } catch (err) {
      showToast(errorMessage(err, "Payment failed."), "error");
    } finally {
      setPayingObligationId(null);
    }
  }

  function autopayMandateFor(occupancyId: number): AutopayMandate | undefined {
    return autopayMandates.find((m) => m.occupancyId === occupancyId && m.status === "ACTIVE");
  }

  async function handleEnableAutopay(occupancy: UserOccupancy) {
    setAutopayBusyOccupancyId(occupancy.id);
    try {
      const mandate = await createAutopayMandate(occupancy.id);
      setAutopayMandates((prev) => [...prev, mandate]);
      showToast("Autopay enabled — future rent will be charged automatically.");
    } catch (err) {
      showToast(errorMessage(err, "Could not enable autopay."), "error");
    } finally {
      setAutopayBusyOccupancyId(null);
    }
  }

  async function handleRevokeAutopay(mandate: AutopayMandate) {
    setAutopayBusyOccupancyId(mandate.occupancyId);
    try {
      const updated = await revokeAutopayMandate(mandate.id);
      setAutopayMandates((prev) => prev.map((m) => (m.id === updated.id ? updated : m)));
      showToast("Autopay turned off.");
    } catch (err) {
      showToast(errorMessage(err, "Could not turn off autopay."), "error");
    } finally {
      setAutopayBusyOccupancyId(null);
    }
  }

  function pendingRequestFor(occupancy: UserOccupancy): BookingChangeRequest | undefined {
    if (!occupancy.agreementId) return undefined;
    return changeRequests.find(
      (cr) => cr.agreementId === occupancy.agreementId && (cr.status === "AWAITING_HOST" || cr.status === "AWAITING_RENTER"),
    );
  }

  function openSublet(occupancy: UserOccupancy) {
    setSubletFor(occupancy);
    setSubletStep(1);
    setProposedEmail("");
    setLookupResult(null);
    setEvidenceRef("");
    setSubletReason("");
    setArrangementType("ASSIGNMENT_FULL");
    setProposedMonthlyRent("");
    setSubletStartDate("");
    setSubletEndDate("");
    setConfirmNoPermissionYet(false);
    setDuplicateSubletRequest(null);
    setError("");
    setSubletUiTerm("sublet");
    getOwnSubletTerminology(occupancy.id)
      .then((r) => setSubletUiTerm(r.uiTerm))
      .catch(() => {});
  }

  function subletStepOneValid(): boolean {
    const rentIsRelevant = (CO_TENANCY_ARRANGEMENT_TYPES as readonly string[]).includes(arrangementType);
    if (!rentIsRelevant || !proposedMonthlyRent.trim()) return true;
    const parsed = Number(proposedMonthlyRent);
    return Number.isFinite(parsed) && parsed > 0;
  }

  function goToSubletStep(step: 1 | 2 | 3) {
    setError("");
    if (step === 2 && !subletStepOneValid()) {
      setError("Proposed monthly rent must be a positive number.");
      return;
    }
    if (step === 3 && (!lookupResult?.found || !lookupResult.identityVerified)) {
      setError(
        !lookupResult?.found
          ? "Look up the proposed renter's email first."
          : "This person needs a verified identity on Zoiko before they can take over a room."
      );
      return;
    }
    setSubletStep(step);
  }

  function openExtension(occupancy: UserOccupancy) {
    setExtensionFor(occupancy);
    setAdditionalMonths("1");
    setExtensionReason("");
    setExtensionError("");
  }

  async function handleExtension(e: FormEvent) {
    e.preventDefault();
    if (!extensionFor?.agreementId) return;
    const months = Number(additionalMonths);
    if (!Number.isInteger(months) || months < 1) {
      setExtensionError("Enter a whole number of months (at least 1).");
      return;
    }
    setExtensionError("");
    setExtensionSubmitting(true);
    try {
      await submitExtensionRequest(extensionFor.agreementId, {
        additionalTermMonths: months,
        reason: extensionReason.trim(),
      });
      setExtensionFor(null);
      showToast("Extension request submitted for host review.");
      await load();
    } catch (err) {
      setExtensionError(errorMessage(err, "Could not submit the extension request."));
    } finally {
      setExtensionSubmitting(false);
    }
  }

  async function openPremises(occupancy: UserOccupancy) {
    setPremisesFor(occupancy);
    setTargetListingId("");
    setPremisesReason("");
    setPremisesError("");
    setAvailableListings([]);
    setListingsLoading(true);
    try {
      const page = await listPublicListings({ limit: 50 });
      setAvailableListings(page.items.filter((l) => l.id !== occupancy.listingId));
    } catch (err) {
      setPremisesError(errorMessage(err, "Could not load available listings."));
    } finally {
      setListingsLoading(false);
    }
  }

  async function handlePremisesChange(e: FormEvent) {
    e.preventDefault();
    if (!premisesFor?.agreementId) return;
    if (!targetListingId) {
      setPremisesError("Choose a listing to move to.");
      return;
    }
    setPremisesError("");
    setPremisesSubmitting(true);
    try {
      await submitPremisesChangeRequest(premisesFor.agreementId, {
        targetListingId, reason: premisesReason.trim(),
      });
      setPremisesFor(null);
      showToast("Room/property change request submitted for host review.");
      await load();
    } catch (err) {
      setPremisesError(errorMessage(err, "Could not submit this request."));
    } finally {
      setPremisesSubmitting(false);
    }
  }

  function openFinancial(occupancy: UserOccupancy) {
    setFinancialFor(occupancy);
    setFinancialProposedRent("");
    setFinancialProposedDeposit("");
    setFinancialReason("");
    setFinancialError("");
  }

  async function handleFinancialChange(e: FormEvent) {
    e.preventDefault();
    if (!financialFor?.agreementId) return;
    const rent = Number(financialProposedRent);
    if (!Number.isFinite(rent) || rent <= 0) {
      setFinancialError("Enter a valid proposed monthly rent.");
      return;
    }
    let deposit: number | undefined;
    if (financialProposedDeposit.trim()) {
      deposit = Number(financialProposedDeposit);
      if (!Number.isFinite(deposit) || deposit < 0) {
        setFinancialError("Enter a valid deposit top-up amount.");
        return;
      }
    }
    setFinancialError("");
    setFinancialSubmitting(true);
    try {
      await submitFinancialChangeRequest(financialFor.agreementId, {
        proposedMonthlyRent: rent, proposedDepositAmount: deposit, reason: financialReason.trim(),
      });
      setFinancialFor(null);
      showToast("Rent change request submitted for host review.");
      await load();
    } catch (err) {
      setFinancialError(errorMessage(err, "Could not submit this request."));
    } finally {
      setFinancialSubmitting(false);
    }
  }

  function openDeposit(occupancy: UserOccupancy) {
    setDepositFor(occupancy);
    setDepositProposedAmount("");
    setDepositReason("");
    setDepositError("");
  }

  async function handleDepositChange(e: FormEvent) {
    e.preventDefault();
    if (!depositFor?.agreementId) return;
    const amount = Number(depositProposedAmount);
    if (!Number.isFinite(amount) || amount < 0) {
      setDepositError("Enter a valid deposit amount.");
      return;
    }
    setDepositError("");
    setDepositSubmitting(true);
    try {
      await submitDepositChangeRequest(depositFor.agreementId, {
        proposedDepositAmount: amount, reason: depositReason.trim(),
      });
      setDepositFor(null);
      showToast("Deposit change request submitted for host review.");
      await load();
    } catch (err) {
      setDepositError(errorMessage(err, "Could not submit this request."));
    } finally {
      setDepositSubmitting(false);
    }
  }

  async function handleWithdraw(bcrId: number) {
    setWithdrawingId(bcrId);
    try {
      await withdrawChangeRequest(bcrId);
      showToast("Request withdrawn.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not withdraw this request."), "error");
    } finally {
      setWithdrawingId(null);
    }
  }

  async function handleAcceptAlternative(bcrId: number) {
    setWithdrawingId(bcrId);
    try {
      await acceptAlternativeChangeTerms(bcrId);
      showToast("Accepted — please review and re-sign the updated agreement.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not accept these terms."), "error");
    } finally {
      setWithdrawingId(null);
    }
  }

  async function handleDeclineAlternative(bcrId: number) {
    setWithdrawingId(bcrId);
    try {
      await declineAlternativeChangeTerms(bcrId);
      showToast("Declined — the request has been withdrawn.");
      await load();
    } catch (err) {
      showToast(errorMessage(err, "Could not decline these terms."), "error");
    } finally {
      setWithdrawingId(null);
    }
  }

  function editEmail(value: string) {
    setProposedEmail(value);
    setLookupResult(null);
    setError("");
  }

  async function handleLookup() {
    if (!proposedEmail.trim()) return;
    setError("");
    setLookingUp(true);
    try {
      setLookupResult(await lookupSubletRenter(proposedEmail.trim()));
    } catch (err) {
      setError(errorMessage(err, "Could not look up that email."));
    } finally {
      setLookingUp(false);
    }
  }

  async function handleSublet(e: FormEvent) {
    e.preventDefault();
    if (!subletFor) return;
    if (!lookupResult?.found || !lookupResult.partyId) {
      setError("Look up the proposed renter's email first.");
      return;
    }
    if (!lookupResult.identityVerified) {
      setError("This person needs a verified identity on Zoiko before they can take over a room.");
      return;
    }
    if (!confirmNoPermissionYet) {
      setError("Confirm you understand this request does not grant permission yet.");
      return;
    }
    const rentIsRelevant = (CO_TENANCY_ARRANGEMENT_TYPES as readonly string[]).includes(arrangementType);
    const parsedRent = proposedMonthlyRent.trim() ? Number(proposedMonthlyRent) : undefined;
    if (rentIsRelevant && proposedMonthlyRent.trim() && (!Number.isFinite(parsedRent) || (parsedRent ?? 0) <= 0)) {
      setError("Proposed monthly rent must be a positive number.");
      return;
    }
    if (subletStartDate && subletEndDate && subletStartDate >= subletEndDate) {
      setError("The proposed start date must be before the proposed end date.");
      return;
    }
    setError("");
    setDuplicateSubletRequest(null);
    setSubmitting(true);
    try {
      await submitSubletRequest(subletFor.id, {
        proposedRenterPartyId: lookupResult.partyId,
        authorityEvidenceRef: evidenceRef.trim(),
        arrangementType,
        reason: subletReason.trim(),
        ...(rentIsRelevant && parsedRent ? { proposedMonthlyRent: parsedRent } : {}),
        ...(subletStartDate ? { proposedStartDate: subletStartDate } : {}),
        ...(subletEndDate ? { proposedEndDate: subletEndDate } : {}),
      });
      setSubletFor(null);
      showToast("Sublet request sent to your host for review.");
    } catch (err) {
      // ZR-SUB-003 Section 15 edge case: "Tenant submits duplicate request |
      // Warn and link existing active request." The backend 409s rather than
      // silently creating a second one -- look up the already-active request
      // for this occupancy so we can link straight to it instead of just
      // showing a dead-end error.
      if (err instanceof ApiError && err.status === 409) {
        try {
          const existing = (await listSubletRequests()).find(
            (r) =>
              r.currentOccupancyId === subletFor.id &&
              !["approved", "rejected", "withdrawn", "expired", "superseded", "cancelled_by_authority"].includes(r.status)
          );
          if (existing) {
            setDuplicateSubletRequest(existing);
            setError("You already have an active sublet request for this rental.");
            return;
          }
        } catch {
          // fall through to the generic error below
        }
      }
      setError(errorMessage(err, "Could not submit the sublet request."));
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <Loader label="Loading your rentals" />;

  if (occupancies.length === 0) {
    return (
      <Card>
        <div className="flex flex-col items-center gap-4 py-10 text-center">
          <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
            <DoorOpen className="h-6 w-6" />
          </span>
          <EmptyState message="You do not have any rentals yet. A rental appears here once an application is approved and the agreement is signed." />
          <Link href="/account/rent">
            <Button size="sm">
              <Search className="h-4 w-4" /> Browse available rooms
            </Button>
          </Link>
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {occupancies.map((occupancy) => (
          <Card key={occupancy.id}>
            <div className="flex items-center justify-between gap-2">
              <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
                {occupancy.listingId}
              </p>
              <Badge tone={occupancyStatusTone[occupancy.status] ?? "neutral"}>{occupancy.status}</Badge>
            </div>
            <p className="mt-0.5 text-xs text-slate-400">Room #{occupancy.roomId}</p>

            <div className="mt-3 space-y-1 text-xs text-slate-500 dark:text-slate-400">
              <p className="flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" /> Moved in{" "}
                {occupancy.moveInDate ? formatDate(occupancy.moveInDate) : "—"}
              </p>
              <p className="flex items-center gap-1.5">
                <CalendarClock className="h-3.5 w-3.5" /> Lease ends{" "}
                {occupancy.expectedEndDate ? formatDate(occupancy.expectedEndDate) : "—"}
              </p>
              {occupancy.moveOutDate && (
                <p className="flex items-center gap-1.5">
                  <CalendarClock className="h-3.5 w-3.5" /> Moved out {formatDate(occupancy.moveOutDate)}
                </p>
              )}
            </div>

            {occupancy.agreementId && (
              <a href={tenantAgreementPdfUrl(occupancy.agreementId)} download className="mt-2 inline-block">
                <Button size="sm" variant="outline">
                  <Download className="h-3.5 w-3.5" /> Download agreement
                </Button>
              </a>
            )}

            {occupancy.status === "ACTIVE" && occupancy.agreementId && (() => {
              const agreementId = occupancy.agreementId!;
              const preview = paymentPreviews[agreementId];
              const due = preview?.amountDueNow.filter((o) => o.status !== "PAID") ?? [];
              const mandate = autopayMandateFor(occupancy.id);
              return (
                <div className="mt-4 space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                  <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    <CreditCard className="h-3.5 w-3.5" /> Rent &amp; payments
                  </p>
                  {due.length === 0 ? (
                    <p className="text-xs text-slate-400">Nothing due right now.</p>
                  ) : (
                    due.map((o) => (
                      <div key={o.id} className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-sm text-slate-700 dark:text-slate-200">
                          {o.obligationType === "RENT" ? "Rent" : o.obligationType === "DEPOSIT" ? "Deposit" : o.obligationType}
                          {" — "}
                          {formatCurrency(o.amount, o.currency)}
                          <span className="text-slate-400"> (due {formatDate(o.dueDate)})</span>
                        </span>
                        <div className="flex items-center gap-2">
                          <select
                            className={`${inputClass} !w-auto py-1.5 text-xs`}
                            value={selectedMethod[o.id] ?? ""}
                            onChange={(e) => setSelectedMethod((prev) => ({ ...prev, [o.id]: e.target.value }))}
                            disabled={!obligationMethods[o.id]?.length}
                          >
                            {(obligationMethods[o.id] ?? []).map((m) => (
                              <option key={m} value={m}>
                                {PAYMENT_METHOD_LABELS[m] ?? m}
                              </option>
                            ))}
                          </select>
                          <Button
                            size="sm"
                            loading={payingObligationId === o.id}
                            disabled={!selectedMethod[o.id]}
                            onClick={() => handlePayObligation(o.id, agreementId)}
                          >
                            Pay now
                          </Button>
                        </div>
                      </div>
                    ))
                  )}
                  <div className="flex items-center justify-between gap-2 border-t border-slate-200 pt-2 dark:border-slate-700">
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      Autopay {mandate ? "is on — rent is charged automatically." : "is off."}
                    </span>
                    {mandate ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={autopayBusyOccupancyId === occupancy.id}
                        onClick={() => handleRevokeAutopay(mandate)}
                      >
                        Turn off
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="outline"
                        loading={autopayBusyOccupancyId === occupancy.id}
                        onClick={() => handleEnableAutopay(occupancy)}
                      >
                        Enable autopay
                      </Button>
                    )}
                  </div>
                </div>
              );
            })()}

            <Button size="sm" variant="ghost" className="mt-3 w-full" onClick={() => setRecordFor(occupancy)}>
              <ClipboardList className="h-3.5 w-3.5" /> View full transaction record
            </Button>

            {occupancy.status === "PENDING_MOVE_IN" && (
              <div className="mt-4 space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Your host is preparing the room for move-in. Once you&apos;ve actually received the keys/room, confirm
                  it below — this is one of the required steps before your host can activate your tenancy.
                </p>
                {receiptConfirmedIds.has(occupancy.id) ? (
                  <Badge tone="success">Receipt confirmed</Badge>
                ) : (
                  <Button
                    size="sm"
                    variant="primary"
                    loading={confirmingReceiptId === occupancy.id}
                    onClick={() => handleConfirmReceipt(occupancy)}
                  >
                    <DoorOpen className="h-3.5 w-3.5" /> I&apos;ve received the room
                  </Button>
                )}
                <Button size="sm" variant="ghost" onClick={() => openCancel(occupancy)}>
                  Cancel this booking
                </Button>
              </div>
            )}

            {(() => {
              const pending = pendingRequestFor(occupancy);
              if (pending) {
                return (
                  <div className="mt-4 space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                    <div className="flex items-center justify-between gap-2">
                      <Badge tone={bookingChangeRequestStatusTone[pending.status] ?? "neutral"}>
                        {bookingChangeTypeLabel[pending.changeType] ?? pending.changeType}
                        {" — "}
                        {pending.status === "AWAITING_RENTER"
                          ? "host proposed different terms"
                          : "pending"}
                      </Badge>
                    </div>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {pending.changeType === "PREMISES_CHANGE" ? (
                        `Move to "${pending.targetListingName || pending.targetListingId}"`
                      ) : pending.changeType === "FINANCIAL_CHANGE" ? (
                        `${formatCurrency(pending.originalMonthlyRent ?? 0, pending.currency)} → ${formatCurrency(pending.proposedMonthlyRent ?? 0, pending.currency)}/month`
                      ) : (
                        `+${pending.additionalTermMonths} month${pending.additionalTermMonths === 1 ? "" : "s"} — new end date ${pending.proposedEndDate ? formatDate(pending.proposedEndDate) : "—"}`
                      )}
                    </p>
                    {pending.status === "AWAITING_RENTER" ? (
                      <>
                        <p className="text-xs font-medium text-primary-900 dark:text-white">
                          Your host proposed different terms — review above and respond.
                        </p>
                        <div className="flex gap-2">
                          <Button
                            size="sm"
                            variant="primary"
                            className="flex-1"
                            loading={withdrawingId === pending.id}
                            onClick={() => handleAcceptAlternative(pending.id)}
                          >
                            Accept
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            className="flex-1"
                            loading={withdrawingId === pending.id}
                            onClick={() => handleDeclineAlternative(pending.id)}
                          >
                            Decline
                          </Button>
                        </div>
                      </>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="w-full"
                        loading={withdrawingId === pending.id}
                        onClick={() => handleWithdraw(pending.id)}
                      >
                        Withdraw request
                      </Button>
                    )}
                  </div>
                );
              }
              if (occupancy.status !== "ACTIVE") return null;
              return (
                <div className="mt-4 flex flex-col gap-2">
                  {occupancy.reassignedViaSubletRequestId ? (
                    <p className="text-xs italic text-slate-400 dark:text-slate-500">
                      This tenancy was already assigned to you via a sublet — it can&apos;t be sublet onward again.
                    </p>
                  ) : (
                    <Button size="sm" variant="outline" onClick={() => openSublet(occupancy)}>
                      <Repeat className="h-3.5 w-3.5" /> Request to sublet
                    </Button>
                  )}
                  {occupancy.agreementId && (
                    <>
                      <Button size="sm" variant="outline" onClick={() => openExtension(occupancy)}>
                        <TrendingUp className="h-3.5 w-3.5" /> Request to extend stay
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => openPremises(occupancy)}>
                        <Building2 className="h-3.5 w-3.5" /> Request a different room/property
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => openFinancial(occupancy)}>
                        Request a rent change
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => openDeposit(occupancy)}>
                        Request a deposit change
                      </Button>
                    </>
                  )}
                </div>
              );
            })()}

            {occupancy.status === "ACTIVE" && (
              <div className="mt-2 flex flex-wrap gap-2">
                {!moveOutNoticeIds.has(occupancy.id) ? (
                  <Button size="sm" variant="ghost" loading={moveOutBusyId === occupancy.id} onClick={() => handleGiveMoveOutNotice(occupancy)}>
                    Give move-out notice
                  </Button>
                ) : !moveOutReadyIds.has(occupancy.id) ? (
                  <Button size="sm" variant="ghost" loading={moveOutBusyId === occupancy.id} onClick={() => handleConfirmMoveOutReady(occupancy)}>
                    I&apos;ve moved out
                  </Button>
                ) : (
                  <Badge tone="success">Move-out confirmed — awaiting host</Badge>
                )}
                <Button size="sm" variant="ghost" onClick={() => openCondition(occupancy)}>
                  Condition report
                </Button>
              </div>
            )}

            {occupancy.status === "ACTIVE" && (() => {
              const terminationCase = terminationCases[occupancy.id];
              if (!terminationCase) {
                return (
                  <div className="mt-2">
                    <Button size="sm" variant="ghost" onClick={() => openTermination(occupancy)}>
                      End my stay
                    </Button>
                  </div>
                );
              }
              const entitlement = refundEntitlements[terminationCase.id];
              return (
                <div className="mt-4 space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                  <div className="flex items-center justify-between gap-2">
                    <Badge tone={terminationCaseStatusTone[terminationCase.status] ?? "neutral"}>
                      {terminationCase.status.replace(/_/g, " ")}
                    </Badge>
                    <span className="text-xs text-slate-400">{terminationCauseCodeLabel[terminationCase.causeCode] ?? terminationCase.causeCode}</span>
                  </div>
                  {terminationCase.effectiveTerminationDate && (
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      Effective {formatDate(terminationCase.effectiveTerminationDate)}
                    </p>
                  )}
                  {terminationCase.status === "SURRENDER_PROPOSED" && (
                    <div className="flex gap-2">
                      <Button
                        size="sm" variant="primary" className="flex-1"
                        loading={terminationActionBusy === terminationCase.id}
                        onClick={() => handleSurrenderResponse(terminationCase.id, true)}
                      >
                        Accept
                      </Button>
                      <Button
                        size="sm" variant="ghost" className="flex-1"
                        loading={terminationActionBusy === terminationCase.id}
                        onClick={() => handleSurrenderResponse(terminationCase.id, false)}
                      >
                        Decline
                      </Button>
                    </div>
                  )}
                  {(terminationCase.status === "OPENED" || terminationCase.status === "PENDING_REVIEW") && (
                    <Button
                      size="sm" variant="ghost" className="w-full"
                      loading={terminationActionBusy === terminationCase.id}
                      onClick={() => handleWithdrawTermination(occupancy.id, terminationCase.id)}
                    >
                      Withdraw request
                    </Button>
                  )}
                  {entitlement && (
                    <div className="space-y-1 border-t border-slate-200 pt-2 dark:border-slate-700">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                        Refund
                      </p>
                      {entitlement.lineItems.filter((li) => li.amount !== 0).map((li) => (
                        <p key={li.id} className="flex items-center justify-between text-xs text-slate-600 dark:text-slate-300">
                          <span>{refundEntitlementLineItemLabel[li.type] ?? li.type}</span>
                          <span>{formatCurrency(li.amount, entitlement.currency)}</span>
                        </p>
                      ))}
                      <p className="flex items-center justify-between text-sm font-semibold text-primary-900 dark:text-white">
                        <span>Net refund</span>
                        <span>{formatCurrency(entitlement.netRefund, entitlement.currency)}</span>
                      </p>
                      <Badge tone={entitlement.status === "EXECUTED" ? "success" : "warning"}>
                        {entitlement.status === "EXECUTED" ? "Refund issued" : "Awaiting host/admin approval"}
                      </Badge>
                    </div>
                  )}
                </div>
              );
            })()}

            <DisputeCasesSection
              client={renterDisputes}
              viewerRole="RENTER"
              cases={disputeCases.filter((c) => c.occupancyId === occupancy.id)}
              showToast={showToast}
              onChanged={load}
              occupancyId={occupancy.id}
              emptyMessage="No disputes on this occupancy."
            />
          </Card>
        ))}
      </div>

      <Modal open={Boolean(terminationFor)} onClose={() => setTerminationFor(null)} title="End My Stay">
        <div className="space-y-3.5">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Tell us why you&apos;re ending your stay, then preview what it means before submitting.
          </p>
          <Field label="Reason">
            <select
              value={terminationCauseCode}
              onChange={(e) => { setTerminationCauseCode(e.target.value); setTerminationPreview(null); }}
              className={inputClass}
            >
              {RENTER_TERMINATION_CAUSE_CODES.map((code) => (
                <option key={code} value={code}>
                  {terminationCauseCodeLabel[code] ?? code}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Notes (optional)">
            <textarea
              value={terminationNotes}
              onChange={(e) => setTerminationNotes(e.target.value)}
              rows={3}
              className={inputClass}
            />
          </Field>
          <Button type="button" variant="outline" fullWidth loading={terminationPreviewing} onClick={handlePreviewTermination}>
            Preview
          </Button>
          {terminationPreview && (
            <div className="space-y-1 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
              <p>Resolved status: {terminationPreview.resolvedStatus.replace(/_/g, " ")}</p>
              {terminationPreview.earliestEffectiveDate && <p>Earliest effective date: {formatDate(terminationPreview.earliestEffectiveDate)}</p>}
              {terminationPreview.estimatedNetRefund !== null && (
                <p className="font-semibold text-primary-900 dark:text-white">
                  Estimated net refund: {formatCurrency(terminationPreview.estimatedNetRefund, terminationFor?.currency ?? "USD")}
                </p>
              )}
              {terminationPreview.estimatedLiabilityNote && <p>{terminationPreview.estimatedLiabilityNote}</p>}
              <p className="italic text-slate-400">{terminationPreview.depositDisclaimer}</p>
            </div>
          )}
          {terminationError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {terminationError}
            </p>
          )}
          <Button type="button" variant="primary" fullWidth loading={terminationSubmitting} onClick={handleSubmitTermination}>
            Submit Request
          </Button>
        </div>
      </Modal>

      <Modal open={Boolean(conditionFor)} onClose={() => setConditionFor(null)} title="Condition Report">
        <div className="space-y-3.5">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Document a room&apos;s condition with a photo and notes — at move-in as a baseline, or at move-out to
            support (or dispute) a deposit claim later.
          </p>

          {conditionItems.length > 0 && (
            <div className="max-h-48 space-y-2 overflow-y-auto rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
              {conditionItems.map((item) => (
                <div key={item.id} className="text-xs text-slate-600 dark:text-slate-300">
                  <span className="font-semibold">{item.reportType === "MOVE_IN" ? "Move-in" : "Move-out"}</span>
                  {item.area ? ` — ${item.area}` : ""}
                  {item.conditionRating ? ` (${item.conditionRating})` : ""}
                  {item.notes ? `: ${item.notes}` : ""}
                  {item.hasFile ? " 📷" : ""}
                </div>
              ))}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <Field label="Type">
              <select value={conditionReportType} onChange={(e) => setConditionReportType(e.target.value as ConditionReportType)} className={inputClass}>
                <option value="MOVE_IN">Move-in</option>
                <option value="MOVE_OUT">Move-out</option>
              </select>
            </Field>
            <Field label="Area (optional)">
              <input type="text" value={conditionArea} onChange={(e) => setConditionArea(e.target.value)} placeholder="e.g. Bedroom" className={inputClass} />
            </Field>
          </div>
          <Field label="Condition (optional)">
            <select value={conditionRating} onChange={(e) => setConditionRating(e.target.value as ConditionRating | "")} className={inputClass}>
              <option value="">Not rated</option>
              <option value="GOOD">Good</option>
              <option value="FAIR">Fair</option>
              <option value="DAMAGED">Damaged</option>
            </select>
          </Field>
          <Field label="Notes">
            <textarea value={conditionNotes} onChange={(e) => setConditionNotes(e.target.value)} rows={2} className={inputClass} />
          </Field>
          <Field label="Photo (optional)">
            <input type="file" accept="image/png,image/jpeg,application/pdf" onChange={(e) => setConditionFile(e.target.files?.[0] ?? null)} className={inputClass} />
          </Field>
          {conditionError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {conditionError}
            </p>
          )}
          <Button type="button" variant="primary" fullWidth loading={conditionSubmitting} onClick={handleAddConditionItem}>
            Add Item
          </Button>
        </div>
      </Modal>

      <Modal
        open={Boolean(subletFor)}
        onClose={() => setSubletFor(null)}
        title={`Request permission to ${subletUiTerm} — Step ${subletStep} of 3`}
      >
        <form onSubmit={handleSublet} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Your host decides this, not Zoiko — we only route the request and record the decision.
          </p>

          {subletStep === 1 && (
            <>
              <Field label="What kind of arrangement is this?">
                <select
                  value={arrangementType}
                  onChange={(e) => setArrangementType(e.target.value as SubletArrangementType)}
                  className={inputClass}
                >
                  {Object.entries(subletArrangementTypeLabel).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </Field>

              {(CO_TENANCY_ARRANGEMENT_TYPES as readonly string[]).includes(arrangementType) && (
                <Field
                  label="Proposed monthly rent for them (optional)"
                  hint="Leave blank to mirror your own rent. Capped by policy — you'll see the max if this is too high."
                >
                  <input
                    type="number"
                    min={0}
                    step="0.01"
                    value={proposedMonthlyRent}
                    onChange={(e) => setProposedMonthlyRent(e.target.value)}
                    placeholder="e.g. 500"
                    className={inputClass}
                  />
                </Field>
              )}

              <div className="grid grid-cols-2 gap-3">
                <Field label="Proposed start date (optional)">
                  <input
                    type="date"
                    value={subletStartDate}
                    onChange={(e) => setSubletStartDate(e.target.value)}
                    className={inputClass}
                  />
                </Field>
                <Field label="Proposed end date (optional)">
                  <input
                    type="date"
                    value={subletEndDate}
                    onChange={(e) => setSubletEndDate(e.target.value)}
                    className={inputClass}
                  />
                </Field>
              </div>

              {error && (
                <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
                  {error}
                </p>
              )}

              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => setSubletFor(null)}>
                  Cancel
                </Button>
                <Button type="button" onClick={() => goToSubletStep(2)}>
                  Continue
                </Button>
              </div>
            </>
          )}

          {subletStep === 2 && (
            <>
              <Field label="Proposed renter's email" hint="The email they use to sign in to Zoiko.">
                <div className="flex gap-2">
                  <input
                    type="email"
                    value={proposedEmail}
                    onChange={(e) => editEmail(e.target.value)}
                    placeholder="them@example.com"
                    className={inputClass}
                  />
                  <Button type="button" variant="outline" loading={lookingUp} onClick={handleLookup}>
                    Look up
                  </Button>
                </div>
              </Field>

              {lookupResult && (
                <p
                  className={
                    lookupResult.found && lookupResult.identityVerified
                      ? "rounded-lg bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-700 ring-1 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/20"
                      : "rounded-lg bg-amber-50 px-3 py-2 text-xs font-medium text-amber-700 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/20"
                  }
                >
                  {!lookupResult.found &&
                    "No Zoiko account found with that email. Ask them to create one first."}
                  {lookupResult.found && !lookupResult.identityVerified &&
                    `Found ${lookupResult.name ?? "this person"}, but they haven't verified their identity yet.`}
                  {lookupResult.found && lookupResult.identityVerified &&
                    `Confirmed: ${lookupResult.name ?? "this person"} (identity verified).`}
                </p>
              )}

              <Field label="Reason (optional)" hint="Only shared with your host, to help them decide.">
                <textarea
                  value={subletReason}
                  onChange={(e) => setSubletReason(e.target.value)}
                  rows={3}
                  placeholder="e.g. I have to relocate for work for 3 months."
                  className={inputClass}
                />
              </Field>

              <Field label="Authority evidence link (optional)" hint="Landlord consent letter or similar, if you have one.">
                <input
                  value={evidenceRef}
                  onChange={(e) => setEvidenceRef(e.target.value)}
                  placeholder="https://..."
                  className={inputClass}
                />
              </Field>

              {error && (
                <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
                  {error}
                </p>
              )}

              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => goToSubletStep(1)}>
                  Back
                </Button>
                <Button type="button" onClick={() => goToSubletStep(3)}>
                  Continue
                </Button>
              </div>
            </>
          )}

          {subletStep === 3 && (
            <>
              <div className="space-y-1.5 rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                <p><span className="text-slate-400">Arrangement:</span> {subletArrangementTypeLabel[arrangementType]}</p>
                <p><span className="text-slate-400">Proposed renter:</span> {lookupResult?.name ?? proposedEmail}</p>
                {(subletStartDate || subletEndDate) && (
                  <p>
                    <span className="text-slate-400">Proposed period:</span>{" "}
                    {subletStartDate ? formatDate(subletStartDate) : "—"} to {subletEndDate ? formatDate(subletEndDate) : "—"}
                  </p>
                )}
                {subletReason && <p><span className="text-slate-400">Reason:</span> {subletReason}</p>}
                {evidenceRef && <p><span className="text-slate-400">Evidence:</span> {evidenceRef}</p>}
              </div>

              <p className="rounded-xl bg-amber-50 px-4 py-3 text-xs font-medium text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                Submitting this request does not give you permission to {subletUiTerm}. Wait for an approval decision
                from your host before proceeding.
              </p>

              <label className="flex items-start gap-2 text-xs text-slate-600 dark:text-slate-300">
                <input
                  type="checkbox"
                  checked={confirmNoPermissionYet}
                  onChange={(e) => setConfirmNoPermissionYet(e.target.checked)}
                  className="mt-0.5"
                />
                I confirm the information above is accurate and understand this does not yet grant permission.
              </label>

              {error && (
                <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
                  {error}
                  {duplicateSubletRequest && (
                    <>
                      {" "}
                      <button
                        type="button"
                        className="font-semibold underline"
                        onClick={() => {
                          setSubletFor(null);
                          router.push("/account/sublets");
                        }}
                      >
                        View your existing request
                      </button>
                      .
                    </>
                  )}
                </p>
              )}

              <div className="flex justify-end gap-2">
                <Button type="button" variant="ghost" onClick={() => goToSubletStep(2)}>
                  Back
                </Button>
                <Button type="submit" loading={submitting} disabled={!confirmNoPermissionYet}>
                  Submit request
                </Button>
              </div>
            </>
          )}
        </form>
      </Modal>

      <Modal open={Boolean(extensionFor)} onClose={() => setExtensionFor(null)} title="Request to extend your stay">
        <form onSubmit={handleExtension} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Your host has to approve this, and you&apos;ll both need to re-sign the updated agreement before it takes
            effect. Current lease end: {extensionFor?.expectedEndDate ? formatDate(extensionFor.expectedEndDate) : "—"}.
          </p>

          <Field label="Additional months" hint="Whole months to add to your current term.">
            <input
              type="number"
              min={1}
              step={1}
              value={additionalMonths}
              onChange={(e) => setAdditionalMonths(e.target.value)}
              className={inputClass}
            />
          </Field>

          {(() => {
            const months = Number(additionalMonths);
            if (!extensionFor?.expectedEndDate || !Number.isInteger(months) || months < 1) return null;
            const proposedEnd = addMonths(extensionFor.expectedEndDate, months);
            return (
              <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                <div className="flex items-center justify-between">
                  <span className="text-slate-500 dark:text-slate-400">Current lease end</span>
                  <span className="font-semibold text-primary-900 dark:text-white">{formatDate(extensionFor.expectedEndDate)}</span>
                </div>
                <div className="mt-1 flex items-center justify-between">
                  <span className="text-slate-500 dark:text-slate-400">Proposed lease end</span>
                  <span className="font-semibold text-primary-900 dark:text-white">{formatDate(proposedEnd)}</span>
                </div>
              </div>
            );
          })()}

          {/* Section 2/11 doctrine: "An extension must not automatically
              charge an increased security deposit merely because rent or
              term increased." -- this platform has no deposit top-up flow at
              all yet, so the honest disclosure is that none happens here. */}
          <p className="text-xs text-slate-400">
            Your security deposit is not affected by this change.
          </p>

          <Field label="Reason (optional)">
            <textarea
              value={extensionReason}
              onChange={(e) => setExtensionReason(e.target.value)}
              rows={3}
              placeholder="e.g. Job here got extended another few months."
              className={inputClass}
            />
          </Field>

          {extensionError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {extensionError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setExtensionFor(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={extensionSubmitting}>
              Submit request
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(premisesFor)} onClose={() => setPremisesFor(null)} title="Request a different room/property">
        <form onSubmit={handlePremisesChange} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Your host has to approve this. Once approved, you&apos;ll go through a normal application on the new
            listing — your current tenancy stays exactly as it is until that new agreement is fully signed.
          </p>

          <Field label="Move to">
            {listingsLoading ? (
              <p className="text-xs text-slate-400">Loading listings...</p>
            ) : (
              <select
                value={targetListingId}
                onChange={(e) => setTargetListingId(e.target.value)}
                className={inputClass}
              >
                <option value="">Choose a listing...</option>
                {availableListings.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name} — {l.city}
                  </option>
                ))}
              </select>
            )}
          </Field>

          {(() => {
            const target = availableListings.find((l) => l.id === targetListingId);
            if (!target) return null;
            return (
              <div className="rounded-xl bg-slate-50 p-3 text-xs dark:bg-slate-800/60">
                <div className="flex items-center justify-between">
                  <span className="text-slate-500 dark:text-slate-400">Listed reference price</span>
                  <span className="font-semibold text-primary-900 dark:text-white">
                    {formatCurrency(target.pricePerNight, target.currency)}/night
                  </span>
                </div>
                <p className="mt-1 text-slate-400">
                  Not your final rent -- your host sets fresh monthly terms once this request is approved.
                </p>
              </div>
            );
          })()}

          <Field label="Reason (optional)">
            <textarea
              value={premisesReason}
              onChange={(e) => setPremisesReason(e.target.value)}
              rows={3}
              className={inputClass}
            />
          </Field>

          {premisesError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {premisesError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setPremisesFor(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={premisesSubmitting}>
              Submit request
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(financialFor)} onClose={() => setFinancialFor(null)} title="Request a rent change">
        <form onSubmit={handleFinancialChange} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Your host has to approve this, and you&apos;ll both need to re-sign the updated agreement before it takes
            effect.
          </p>

          <Field label={`Proposed monthly rent${financialFor?.currency ? ` (${financialFor.currency})` : ""}`}>
            <input
              type="number"
              min={0}
              step="0.01"
              value={financialProposedRent}
              onChange={(e) => setFinancialProposedRent(e.target.value)}
              className={inputClass}
            />
          </Field>

          <Field label={`Deposit top-up (optional)${financialFor?.currency ? ` (${financialFor.currency})` : ""}`}>
            <input
              type="number"
              min={0}
              step="0.01"
              placeholder="Leave blank to keep the current deposit"
              value={financialProposedDeposit}
              onChange={(e) => setFinancialProposedDeposit(e.target.value)}
              className={inputClass}
            />
          </Field>
          <p className="text-xs text-slate-400">
            Your security deposit only changes if you enter a top-up amount above -- it must be equal to or greater
            than your current deposit.
          </p>

          <Field label="Reason (optional)">
            <textarea
              value={financialReason}
              onChange={(e) => setFinancialReason(e.target.value)}
              rows={3}
              className={inputClass}
            />
          </Field>

          {financialError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {financialError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setFinancialFor(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={financialSubmitting}>
              Submit request
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(cancelFor)} onClose={() => setCancelFor(null)} title="Cancel This Booking">
        <div className="space-y-3.5">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Cancelling a booking you haven&apos;t moved into yet refunds what you&apos;ve already paid, minus any
            cancellation fee if you&apos;re outside the free-cancellation window. This cannot be undone.
          </p>
          <Field label="Reason (optional)">
            <textarea
              value={cancelReason}
              onChange={(e) => setCancelReason(e.target.value)}
              rows={3}
              className={inputClass}
            />
          </Field>
          {cancelError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {cancelError}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setCancelFor(null)}>
              Keep booking
            </Button>
            <Button type="button" variant="primary" loading={cancelSubmitting} onClick={handleCancelBeforeMoveIn}>
              Cancel booking
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(depositFor)} onClose={() => setDepositFor(null)} title="Request a deposit change">
        <form onSubmit={handleDepositChange} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Your host has to approve this. Approval alone doesn&apos;t change the amount held — the actual deposit
            adjustment is processed separately under your market&apos;s deposit rules.
          </p>

          <Field label={`Proposed deposit amount${depositFor?.currency ? ` (${depositFor.currency})` : ""}`}>
            <input
              type="number"
              min={0}
              step="0.01"
              value={depositProposedAmount}
              onChange={(e) => setDepositProposedAmount(e.target.value)}
              className={inputClass}
            />
          </Field>

          <Field label="Reason (optional)">
            <textarea
              value={depositReason}
              onChange={(e) => setDepositReason(e.target.value)}
              rows={3}
              className={inputClass}
            />
          </Field>

          {depositError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {depositError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setDepositFor(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={depositSubmitting}>
              Submit request
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(recordFor)} onClose={() => setRecordFor(null)} title="Rental transaction record" size="xl">
        {recordFor && <RentalTransactionRecord occupancyId={recordFor.id} role="renter" />}
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}
