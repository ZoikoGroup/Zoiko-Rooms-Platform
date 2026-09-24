"use client";

import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Globe2 } from "lucide-react";
import { MarketPolicyPack } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { formatDate } from "@/lib/utils";

const CONFIDENCE_TONE = {
  VERIFIED: "success",
  REVIEW_REQUIRED: "warning",
  DEPRECATED: "neutral",
  EMERGENCY_BLOCK: "danger",
} as const;

const emptyForm = {
  jurisdictionCode: "",
  effectiveFrom: new Date().toISOString().slice(0, 10),
  confidence: "REVIEW_REQUIRED",
  legalSourceNote: "",
  occupancyEligibilityRequired: false,
  occupancyEligibilityMethodNote: "",
  occupancyEligibilityFollowUpDays: "",
  identityEvidenceRetentionDays: "90",
  identityRequiredAtApplication: false,
  requiredPropertyComplianceCodes: "",
  screeningProhibitedCheckTypes: "",

  depositInstrumentAllowed: "OPTIONAL",
  depositMaxRentMultiple: "3",
  depositCustodyModel: "HOST_OR_AGENT",
  depositProtectionDeadlineDays: "",
  depositReleaseDeadlineDays: "30",

  subletConsentStandard: "STATUTORY_RESPONSE_DEADLINE",
  subletConsentResponseDays: "14",
  subletMaxRentMultipleOfOriginal: "1",
  subletAssignmentPayeeModel: "HOST_OR_LANDLORD_PAYEE",
  subletSubleasePayeeModel: "ORIGINAL_RENTER_PAYEE",

  rentChangeMinIntervalDays: "365",

  fundsFlowProfile: "DIRECT_SETTLEMENT",
  permittedPaymentMethodClasses: "",
  zoikoLegalEntityName: "Zoiko Realty Group",
  zoikoTaxRegistrationNumber: "",
  serviceFeeTaxRate: "0",

  terminationNoticeDays: "30",
  alignTerminationToRentCycle: false,
  terminationLiabilityModel: "NOTICE_RENT",
  terminationBreakFeeRentMultiple: "0",
  terminationLiabilityCapRentMultiple: "",

  disputeDepositAuthorityClass: "A2",
  disputeBookingAgreementAuthorityClass: "A1",
  disputePropertyConditionAuthorityClass: "A1",
  disputeSubletOccupancyAuthorityClass: "A1",
  disputeResponseWindowDays: "5",
  disputeEvidenceWindowDays: "14",
  disputeExternalFilingDeadlineDays: "",
  disputeConciliationRequirement: "NOT_REQUIRED",
  disputeNonWaivableClaimFamilies: "",
};

/** Admin CRUD for MarketPolicyPack -- ZR-ENG-CLR-012 Section 34's own
 * "Jurisdiction pack missing required source/effective date fails
 * deployment validation" (QA-52) starts here: this is the only way a
 * jurisdiction pack (England's own included) can exist in production at
 * all. Every jurisdiction-aware domain (deposit, sublet, rent-change,
 * occupancy eligibility, property compliance, screening) resolves its
 * rules from what's created here -- see crud/market_policy.py's
 * resolve_market_policy(). Every configurable dimension (verification,
 * deposit, sublet, rent-change, payment/fee, termination, dispute-forum
 * overrides) is exposed in this form -- a jurisdiction pack is never
 * stuck on the model's Python-level defaults just because the admin
 * surface didn't expose a field to change it. */
export function MarketPolicyPackManager() {
  const [packs, setPacks] = useState<MarketPolicyPack[]>([]);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState("");

  const [createOpen, setCreateOpen] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [creating, setCreating] = useState(false);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3400);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await apiClientFetch<MarketPolicyPack[]>("/api/market-policy-packs");
      setPacks(data);
    } catch {
      showToast("Failed to load market policy packs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  function openCreate() {
    setForm(emptyForm);
    setCreateOpen(true);
  }

  async function submitCreate() {
    if (!form.jurisdictionCode.trim()) return showToast("Jurisdiction code is required");
    if (form.jurisdictionCode.trim().length > 10) return showToast("Jurisdiction code must be 10 characters or fewer");

    setCreating(true);
    try {
      await apiClientFetch("/api/market-policy-packs", {
        method: "POST",
        body: JSON.stringify({
          jurisdictionCode: form.jurisdictionCode.trim(),
          effectiveFrom: form.effectiveFrom,
          confidence: form.confidence,
          legalSourceNote: form.legalSourceNote.trim(),
          occupancyEligibilityRequired: form.occupancyEligibilityRequired,
          occupancyEligibilityMethodNote: form.occupancyEligibilityMethodNote.trim(),
          occupancyEligibilityFollowUpDays: form.occupancyEligibilityFollowUpDays ? Number(form.occupancyEligibilityFollowUpDays) : null,
          identityEvidenceRetentionDays: Number(form.identityEvidenceRetentionDays) || 90,
          identityRequiredAtApplication: form.identityRequiredAtApplication,
          requiredPropertyComplianceCodes: form.requiredPropertyComplianceCodes
            .split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
          screeningProhibitedCheckTypes: form.screeningProhibitedCheckTypes
            .split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),

          depositInstrumentAllowed: form.depositInstrumentAllowed,
          depositMaxRentMultiple: Number(form.depositMaxRentMultiple) || 0,
          depositCustodyModel: form.depositCustodyModel,
          depositProtectionDeadlineDays: form.depositProtectionDeadlineDays ? Number(form.depositProtectionDeadlineDays) : null,
          depositReleaseDeadlineDays: Number(form.depositReleaseDeadlineDays) || 0,

          subletConsentStandard: form.subletConsentStandard,
          subletConsentResponseDays: Number(form.subletConsentResponseDays) || 0,
          subletMaxRentMultipleOfOriginal: Number(form.subletMaxRentMultipleOfOriginal) || 0,
          subletAssignmentPayeeModel: form.subletAssignmentPayeeModel,
          subletSubleasePayeeModel: form.subletSubleasePayeeModel,

          rentChangeMinIntervalDays: Number(form.rentChangeMinIntervalDays) || 0,

          fundsFlowProfile: form.fundsFlowProfile,
          permittedPaymentMethodClasses: form.permittedPaymentMethodClasses
            .split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
          zoikoLegalEntityName: form.zoikoLegalEntityName.trim(),
          zoikoTaxRegistrationNumber: form.zoikoTaxRegistrationNumber.trim(),
          serviceFeeTaxRate: Number(form.serviceFeeTaxRate) || 0,

          terminationNoticeDays: Number(form.terminationNoticeDays) || 0,
          alignTerminationToRentCycle: form.alignTerminationToRentCycle,
          terminationLiabilityModel: form.terminationLiabilityModel,
          terminationBreakFeeRentMultiple: Number(form.terminationBreakFeeRentMultiple) || 0,
          terminationLiabilityCapRentMultiple: form.terminationLiabilityCapRentMultiple
            ? Number(form.terminationLiabilityCapRentMultiple) : null,

          disputeDepositAuthorityClass: form.disputeDepositAuthorityClass.trim(),
          disputeBookingAgreementAuthorityClass: form.disputeBookingAgreementAuthorityClass.trim(),
          disputePropertyConditionAuthorityClass: form.disputePropertyConditionAuthorityClass.trim(),
          disputeSubletOccupancyAuthorityClass: form.disputeSubletOccupancyAuthorityClass.trim(),
          disputeResponseWindowDays: Number(form.disputeResponseWindowDays) || 0,
          disputeEvidenceWindowDays: Number(form.disputeEvidenceWindowDays) || 0,
          disputeExternalFilingDeadlineDays: form.disputeExternalFilingDeadlineDays
            ? Number(form.disputeExternalFilingDeadlineDays) : null,
          disputeConciliationRequirement: form.disputeConciliationRequirement,
          disputeNonWaivableClaimFamilies: form.disputeNonWaivableClaimFamilies
            .split(",").map((s) => s.trim().toUpperCase()).filter(Boolean),
        }),
      });
      showToast("Market policy pack created.");
      setCreateOpen(false);
      await load();
    } catch {
      showToast("Failed to create market policy pack");
    } finally {
      setCreating(false);
    }
  }

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Globe2 className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Market Policy Packs</h2>
        </div>
        <Button size="sm" variant="primary" onClick={openCreate}>
          New pack
        </Button>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Every jurisdiction-aware rule (deposit, sublet, rent-change, occupancy eligibility, property compliance,
        screening) resolves from a pack here — never hard-coded per country. REVIEW_REQUIRED means placeholder
        values, not legal sign-off.
      </p>

      <div className="mt-4 space-y-2">
        {loading ? (
          <p className="text-sm text-slate-400">Loading...</p>
        ) : packs.length === 0 ? (
          <p className="text-sm text-slate-400">No market policy packs configured yet.</p>
        ) : (
          packs.map((pack) => (
            <div
              key={pack.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {pack.jurisdictionCode} <span className="text-xs font-normal text-slate-400">v{pack.version}</span>
                </p>
                <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                  Effective {formatDate(pack.effectiveFrom)}
                  {pack.effectiveTo && ` – ${formatDate(pack.effectiveTo)}`}
                  {pack.occupancyEligibilityRequired && " · Occupancy eligibility required"}
                  {pack.identityRequiredAtApplication && " · ID required at application"}
                </p>
                {pack.legalSourceNote && <p className="mt-0.5 text-xs text-slate-400">{pack.legalSourceNote}</p>}
              </div>
              <Badge tone={CONFIDENCE_TONE[pack.confidence] ?? "neutral"}>{pack.confidence}</Badge>
            </div>
          ))
        )}
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New market policy pack">
        <div className="space-y-3.5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Jurisdiction code</label>
              <input
                value={form.jurisdictionCode}
                onChange={(e) => setForm({ ...form, jurisdictionCode: e.target.value })}
                placeholder="e.g. France, US, Australia"
                maxLength={10}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Effective from</label>
              <input
                type="date"
                value={form.effectiveFrom}
                onChange={(e) => setForm({ ...form, effectiveFrom: e.target.value })}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Confidence</label>
            <select
              value={form.confidence}
              onChange={(e) => setForm({ ...form, confidence: e.target.value })}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="REVIEW_REQUIRED">Review required (placeholder values)</option>
              <option value="VERIFIED">Verified (checked against real regulator guidance)</option>
              <option value="DEPRECATED">Deprecated</option>
              <option value="EMERGENCY_BLOCK">Emergency block</option>
            </select>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Legal source note</label>
            <textarea
              value={form.legalSourceNote}
              onChange={(e) => setForm({ ...form, legalSourceNote: e.target.value })}
              rows={2}
              placeholder="e.g. GOV.UK Home Office right-to-rent guidance, reviewed Aug 2026"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>

          <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Verification policy (Section 12)
            </p>
            <div className="space-y-2.5">
              <label className="flex items-center gap-2 text-sm text-primary-900 dark:text-white">
                <input
                  type="checkbox"
                  checked={form.occupancyEligibilityRequired}
                  onChange={(e) => setForm({ ...form, occupancyEligibilityRequired: e.target.checked })}
                />
                Occupancy eligibility required
              </label>
              {form.occupancyEligibilityRequired && (
                <div className="grid grid-cols-2 gap-2 pl-6">
                  <input
                    value={form.occupancyEligibilityMethodNote}
                    onChange={(e) => setForm({ ...form, occupancyEligibilityMethodNote: e.target.value })}
                    placeholder="Method note"
                    className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                  />
                  <input
                    type="number"
                    value={form.occupancyEligibilityFollowUpDays}
                    onChange={(e) => setForm({ ...form, occupancyEligibilityFollowUpDays: e.target.value })}
                    placeholder="Follow-up days"
                    className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                  />
                </div>
              )}
              <label className="flex items-center gap-2 text-sm text-primary-900 dark:text-white">
                <input
                  type="checkbox"
                  checked={form.identityRequiredAtApplication}
                  onChange={(e) => setForm({ ...form, identityRequiredAtApplication: e.target.checked })}
                />
                Identity required at application
              </label>
              <div>
                <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Identity evidence retention (days)</label>
                <input
                  type="number"
                  value={form.identityEvidenceRetentionDays}
                  onChange={(e) => setForm({ ...form, identityEvidenceRetentionDays: e.target.value })}
                  className="w-32 rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Required property compliance codes (comma-separated)</label>
                <input
                  value={form.requiredPropertyComplianceCodes}
                  onChange={(e) => setForm({ ...form, requiredPropertyComplianceCodes: e.target.value })}
                  placeholder="e.g. GAS_SAFETY_CERT, EPC"
                  className="w-full rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                />
              </div>
              <div>
                <label className="mb-1 block text-xs text-slate-500 dark:text-slate-400">Prohibited screening check types (comma-separated)</label>
                <input
                  value={form.screeningProhibitedCheckTypes}
                  onChange={(e) => setForm({ ...form, screeningProhibitedCheckTypes: e.target.value })}
                  placeholder="e.g. CRIMINAL_RECORD"
                  className="w-full rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
                />
              </div>
            </div>
          </div>

          <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Deposit policy
            </p>
            <div className="grid grid-cols-2 gap-2">
              <input
                value={form.depositInstrumentAllowed}
                onChange={(e) => setForm({ ...form, depositInstrumentAllowed: e.target.value })}
                placeholder="Instrument allowed (e.g. OPTIONAL)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                value={form.depositCustodyModel}
                onChange={(e) => setForm({ ...form, depositCustodyModel: e.target.value })}
                placeholder="Custody model (e.g. HOST_OR_AGENT)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number" step="0.1"
                value={form.depositMaxRentMultiple}
                onChange={(e) => setForm({ ...form, depositMaxRentMultiple: e.target.value })}
                placeholder="Max rent multiple"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number"
                value={form.depositProtectionDeadlineDays}
                onChange={(e) => setForm({ ...form, depositProtectionDeadlineDays: e.target.value })}
                placeholder="Protection deadline (days, optional)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number"
                value={form.depositReleaseDeadlineDays}
                onChange={(e) => setForm({ ...form, depositReleaseDeadlineDays: e.target.value })}
                placeholder="Release deadline (days)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
            </div>
          </div>

          <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Sublet policy
            </p>
            <div className="grid grid-cols-2 gap-2">
              <input
                value={form.subletConsentStandard}
                onChange={(e) => setForm({ ...form, subletConsentStandard: e.target.value })}
                placeholder="Consent standard"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number"
                value={form.subletConsentResponseDays}
                onChange={(e) => setForm({ ...form, subletConsentResponseDays: e.target.value })}
                placeholder="Consent response (days)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number" step="0.1"
                value={form.subletMaxRentMultipleOfOriginal}
                onChange={(e) => setForm({ ...form, subletMaxRentMultipleOfOriginal: e.target.value })}
                placeholder="Max rent multiple of original"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                value={form.subletAssignmentPayeeModel}
                onChange={(e) => setForm({ ...form, subletAssignmentPayeeModel: e.target.value })}
                placeholder="Assignment payee model"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                value={form.subletSubleasePayeeModel}
                onChange={(e) => setForm({ ...form, subletSubleasePayeeModel: e.target.value })}
                placeholder="Sublease payee model"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number"
                value={form.rentChangeMinIntervalDays}
                onChange={(e) => setForm({ ...form, rentChangeMinIntervalDays: e.target.value })}
                placeholder="Min rent-change interval (days)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
            </div>
          </div>

          <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Rental payment policy
            </p>
            <div className="grid grid-cols-2 gap-2">
              <p className="col-span-2 text-xs text-slate-500 dark:text-slate-400">
                Renters pay the verified recipient directly — Zoiko Rooms takes no commission and never collects, holds
                or pays out rent or deposits (ZR-PAY-CFG-001). The Listing Fee, its tax and billing entity are set in
                Finance → Listing Fee Price Book.
              </p>
              <input
                value={form.permittedPaymentMethodClasses}
                onChange={(e) => setForm({ ...form, permittedPaymentMethodClasses: e.target.value })}
                placeholder="Methods renters may use to pay the recipient (comma-separated, e.g. CARD,BANK_TRANSFER)"
                className="col-span-2 rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
            </div>
          </div>

          <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Termination policy (Section 6)
            </p>
            <div className="grid grid-cols-2 gap-2">
              <input
                type="number"
                value={form.terminationNoticeDays}
                onChange={(e) => setForm({ ...form, terminationNoticeDays: e.target.value })}
                placeholder="Notice period (days)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <select
                value={form.terminationLiabilityModel}
                onChange={(e) => setForm({ ...form, terminationLiabilityModel: e.target.value })}
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              >
                <option value="NOTICE_RENT">Notice rent</option>
                <option value="STATUTORY_BREAK_FEE">Statutory break fee</option>
                <option value="CONTRACT_BREAK_AMOUNT">Contract break amount</option>
                <option value="ACTUAL_REASONABLE_LOSS">Actual reasonable loss</option>
                <option value="CAPPED_COMPENSATION">Capped compensation</option>
                <option value="ZERO_LIABILITY">Zero liability</option>
                <option value="MIXED">Mixed</option>
              </select>
              <input
                type="number" step="0.1"
                value={form.terminationBreakFeeRentMultiple}
                onChange={(e) => setForm({ ...form, terminationBreakFeeRentMultiple: e.target.value })}
                placeholder="Break-fee rent multiple"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number" step="0.1"
                value={form.terminationLiabilityCapRentMultiple}
                onChange={(e) => setForm({ ...form, terminationLiabilityCapRentMultiple: e.target.value })}
                placeholder="Liability cap rent multiple (optional)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <label className="col-span-2 flex items-center gap-2 text-sm text-primary-900 dark:text-white">
                <input
                  type="checkbox"
                  checked={form.alignTerminationToRentCycle}
                  onChange={(e) => setForm({ ...form, alignTerminationToRentCycle: e.target.checked })}
                />
                Align termination to rent cycle
              </label>
            </div>
          </div>

          <div className="rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Dispute-forum overrides (Section 10, optional)
            </p>
            <div className="grid grid-cols-2 gap-2">
              <input
                value={form.disputeDepositAuthorityClass}
                onChange={(e) => setForm({ ...form, disputeDepositAuthorityClass: e.target.value })}
                placeholder="Deposit authority class (e.g. A2)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                value={form.disputeBookingAgreementAuthorityClass}
                onChange={(e) => setForm({ ...form, disputeBookingAgreementAuthorityClass: e.target.value })}
                placeholder="Booking/agreement authority class"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                value={form.disputePropertyConditionAuthorityClass}
                onChange={(e) => setForm({ ...form, disputePropertyConditionAuthorityClass: e.target.value })}
                placeholder="Property condition authority class"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                value={form.disputeSubletOccupancyAuthorityClass}
                onChange={(e) => setForm({ ...form, disputeSubletOccupancyAuthorityClass: e.target.value })}
                placeholder="Sublet/occupancy authority class"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number"
                value={form.disputeResponseWindowDays}
                onChange={(e) => setForm({ ...form, disputeResponseWindowDays: e.target.value })}
                placeholder="Response window (days)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number"
                value={form.disputeEvidenceWindowDays}
                onChange={(e) => setForm({ ...form, disputeEvidenceWindowDays: e.target.value })}
                placeholder="Evidence window (days)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <input
                type="number"
                value={form.disputeExternalFilingDeadlineDays}
                onChange={(e) => setForm({ ...form, disputeExternalFilingDeadlineDays: e.target.value })}
                placeholder="External filing deadline (days, optional)"
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
              <select
                value={form.disputeConciliationRequirement}
                onChange={(e) => setForm({ ...form, disputeConciliationRequirement: e.target.value })}
                className="rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              >
                <option value="NOT_REQUIRED">Conciliation not required</option>
                <option value="OPTIONAL">Conciliation optional</option>
                <option value="MANDATORY">Conciliation mandatory</option>
              </select>
              <input
                value={form.disputeNonWaivableClaimFamilies}
                onChange={(e) => setForm({ ...form, disputeNonWaivableClaimFamilies: e.target.value })}
                placeholder="Non-waivable claim families (comma-separated)"
                className="col-span-2 rounded-lg bg-white px-3 py-2 text-xs outline-none ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700"
              />
            </div>
          </div>

          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setCreateOpen(false)}>
              Cancel
            </Button>
            <Button variant="primary" loading={creating} onClick={submitCreate}>
              Create pack
            </Button>
          </div>
        </div>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </section>
  );
}
