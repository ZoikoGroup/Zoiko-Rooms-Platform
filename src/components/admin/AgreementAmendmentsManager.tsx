"use client";

import { useState } from "react";
import { CheckCircle2, FileEdit } from "lucide-react";
import { AgreementAmendment, AgreementParty, AmendmentType } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { amendmentStatusLabel, amendmentStatusTone, amendmentTypeLabel } from "@/lib/status";
import { formatDate } from "@/lib/utils";

const AMENDMENT_TYPES: AmendmentType[] = [
  "MATERIAL_CHANGE", "ADDENDUM", "ASSIGNMENT_NOVATION", "RESTATED_AGREEMENT", "RENEWAL", "CORRECTION",
];

/** ZR-ENG-CLR-004 Section 9.4/ZR-ENG-CLR-012 Section 15: post-execution
 * amendments (rent/date/term changes and, as an ADDENDUM, adding a
 * guarantor). No agreement-detail screen exists yet in this admin console,
 * so this is looked up by agreement ID -- same pattern as the other
 * verification managers (party/room lookup). */
export function AgreementAmendmentsManager() {
  const [agreementIdInput, setAgreementIdInput] = useState("");
  const [agreementId, setAgreementId] = useState<number | null>(null);
  const [amendments, setAmendments] = useState<AgreementAmendment[]>([]);
  const [parties, setParties] = useState<AgreementParty[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState("");

  const [requestReason, setRequestReason] = useState("");
  const [requesting, setRequesting] = useState(false);

  const [classifyTarget, setClassifyTarget] = useState<AgreementAmendment | null>(null);
  const [amendmentType, setAmendmentType] = useState<AmendmentType>("ADDENDUM");
  const [classifying, setClassifying] = useState(false);

  const [proposeTermsTarget, setProposeTermsTarget] = useState<AgreementAmendment | null>(null);
  const [monthlyRent, setMonthlyRent] = useState("");
  const [depositAmount, setDepositAmount] = useState("");
  const [startDate, setStartDate] = useState("");
  const [termMonths, setTermMonths] = useState("");
  const [proposingTerms, setProposingTerms] = useState(false);

  const [proposeGuarantorTarget, setProposeGuarantorTarget] = useState<AgreementAmendment | null>(null);
  const [guarantorName, setGuarantorName] = useState("");
  const [guarantorEmail, setGuarantorEmail] = useState("");
  const [proposingGuarantor, setProposingGuarantor] = useState(false);

  const [approvingId, setApprovingId] = useState<number | null>(null);

  const [consentTarget, setConsentTarget] = useState<AgreementParty | null>(null);
  const [consentEvidenceRef, setConsentEvidenceRef] = useState("");
  const [recordingConsent, setRecordingConsent] = useState(false);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  async function loadForAgreement(id: number) {
    setLoading(true);
    try {
      const [amendmentData, partyData] = await Promise.all([
        apiClientFetch<AgreementAmendment[]>(`/api/leasing/agreements/${id}/amendments`),
        apiClientFetch<AgreementParty[]>(`/api/leasing/agreements/${id}/parties`),
      ]);
      setAmendments(amendmentData);
      setParties(partyData);
      setAgreementId(id);
    } catch {
      showToast("Failed to load this agreement");
    } finally {
      setLoading(false);
    }
  }

  function handleLookup() {
    const id = Number(agreementIdInput);
    if (!Number.isInteger(id) || id <= 0) return showToast("Enter a valid agreement ID");
    loadForAgreement(id);
  }

  async function submitRequestAmendment() {
    if (!agreementId) return;
    setRequesting(true);
    try {
      await apiClientFetch(`/api/leasing/agreements/${agreementId}/amendments`, {
        method: "POST",
        body: JSON.stringify({ reason: requestReason.trim() }),
      });
      showToast("Amendment requested.");
      setRequestReason("");
      await loadForAgreement(agreementId);
    } catch {
      showToast("Failed to request amendment");
    } finally {
      setRequesting(false);
    }
  }

  function openClassifyModal(amendment: AgreementAmendment) {
    setClassifyTarget(amendment);
    setAmendmentType("ADDENDUM");
  }

  async function submitClassify() {
    if (!classifyTarget || !agreementId) return;
    setClassifying(true);
    try {
      await apiClientFetch(`/api/leasing/agreements/${agreementId}/amendments/${classifyTarget.id}/classify`, {
        method: "POST",
        body: JSON.stringify({ amendmentType }),
      });
      showToast("Amendment classified.");
      setClassifyTarget(null);
      await loadForAgreement(agreementId);
    } catch {
      showToast("Failed to classify amendment");
    } finally {
      setClassifying(false);
    }
  }

  function openProposeTermsModal(amendment: AgreementAmendment) {
    setProposeTermsTarget(amendment);
    setMonthlyRent("");
    setDepositAmount("");
    setStartDate("");
    setTermMonths("");
  }

  async function submitProposeTerms() {
    if (!proposeTermsTarget || !agreementId) return;
    const proposedTerms: Record<string, unknown> = {};
    if (monthlyRent) proposedTerms.monthlyRent = Number(monthlyRent);
    if (depositAmount) proposedTerms.depositAmount = Number(depositAmount);
    if (startDate) proposedTerms.startDate = startDate;
    if (termMonths) proposedTerms.termMonths = Number(termMonths);
    if (Object.keys(proposedTerms).length === 0) return showToast("Propose at least one changed term");

    setProposingTerms(true);
    try {
      await apiClientFetch(`/api/leasing/agreements/${agreementId}/amendments/${proposeTermsTarget.id}/propose-terms`, {
        method: "POST",
        body: JSON.stringify({ proposedTerms }),
      });
      showToast("Terms proposed.");
      setProposeTermsTarget(null);
      await loadForAgreement(agreementId);
    } catch {
      showToast("Failed to propose terms");
    } finally {
      setProposingTerms(false);
    }
  }

  function openProposeGuarantorModal(amendment: AgreementAmendment) {
    setProposeGuarantorTarget(amendment);
    setGuarantorName("");
    setGuarantorEmail("");
  }

  async function submitProposeGuarantor() {
    if (!proposeGuarantorTarget || !agreementId) return;
    if (!guarantorName.trim()) return showToast("Guarantor legal name is required");

    setProposingGuarantor(true);
    try {
      await apiClientFetch(`/api/leasing/agreements/${agreementId}/amendments/${proposeGuarantorTarget.id}/propose-guarantor`, {
        method: "POST",
        body: JSON.stringify({ legalName: guarantorName.trim(), contactEmail: guarantorEmail.trim() }),
      });
      showToast("Guarantor proposed.");
      setProposeGuarantorTarget(null);
      await loadForAgreement(agreementId);
    } catch {
      showToast("Failed to propose guarantor");
    } finally {
      setProposingGuarantor(false);
    }
  }

  async function approveAmendment(amendment: AgreementAmendment) {
    if (!agreementId) return;
    setApprovingId(amendment.id);
    try {
      await apiClientFetch(`/api/leasing/agreements/${agreementId}/amendments/${amendment.id}/approve`, { method: "POST" });
      showToast("Amendment approved — a new version was generated and re-signature is now required.");
      await loadForAgreement(agreementId);
    } catch {
      showToast("Failed to approve amendment");
    } finally {
      setApprovingId(null);
    }
  }

  function openConsentModal(party: AgreementParty) {
    setConsentTarget(party);
    setConsentEvidenceRef("");
  }

  async function submitConsent() {
    if (!consentTarget || !agreementId) return;
    if (!consentEvidenceRef.trim()) return showToast("Evidence reference is required");
    setRecordingConsent(true);
    try {
      await apiClientFetch(`/api/leasing/agreements/${agreementId}/parties/${consentTarget.id}/guarantor-consent`, {
        method: "POST",
        body: JSON.stringify({ evidenceRef: consentEvidenceRef.trim(), method: "WET_INK" }),
      });
      showToast("Guarantor consent recorded.");
      setConsentTarget(null);
      await loadForAgreement(agreementId);
    } catch {
      showToast("Failed to record consent");
    } finally {
      setRecordingConsent(false);
    }
  }

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <FileEdit className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Agreement Amendments</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Post-signature changes to a SIGNED agreement — rent/date/term changes, or adding a guarantor as an addendum.
        Every change produces a new immutable version and requires re-signature.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <div>
          <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Agreement ID</label>
          <input
            type="number"
            value={agreementIdInput}
            onChange={(e) => setAgreementIdInput(e.target.value)}
            className="w-32 rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
        </div>
        <Button size="sm" variant="outline" loading={loading} onClick={handleLookup}>
          Look up
        </Button>
      </div>

      {agreementId !== null && (
        <div className="mt-5 space-y-5">
          {parties.length > 0 && (
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Parties</h3>
              <div className="mt-2 space-y-2">
                {parties.map((party) => (
                  <div
                    key={party.id}
                    className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-primary-900 dark:text-white">
                        {party.legalName || "(unnamed)"} <span className="text-xs font-normal text-slate-400">· {party.role}</span>
                      </p>
                      <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{party.contactEmail}</p>
                    </div>
                    {party.role === "guarantor" && (
                      party.consentedAt ? (
                        <Badge tone="success">Consented {formatDate(party.consentedAt)}</Badge>
                      ) : (
                        <Button size="sm" variant="primary" onClick={() => openConsentModal(party)}>
                          Record consent
                        </Button>
                      )
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div>
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">Amendments</h3>
            </div>
            <div className="mt-2 flex flex-wrap items-end gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
              <div className="flex-1 min-w-[12rem]">
                <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Reason for a new amendment</label>
                <input
                  value={requestReason}
                  onChange={(e) => setRequestReason(e.target.value)}
                  placeholder="e.g. Landlord requires a guarantor"
                  className="w-full rounded-xl bg-white px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
                />
              </div>
              <Button size="sm" variant="primary" loading={requesting} onClick={submitRequestAmendment}>
                Request amendment
              </Button>
            </div>

            <div className="mt-2 space-y-2">
              {amendments.length === 0 ? (
                <p className="text-sm text-slate-400">No amendments for this agreement yet.</p>
              ) : (
                amendments.map((amendment) => (
                  <div key={amendment.id} className="rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-primary-900 dark:text-white">
                          {amendment.amendmentType ? amendmentTypeLabel[amendment.amendmentType] : "Not yet classified"}
                        </p>
                        <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{amendment.reason}</p>
                        <p className="mt-0.5 text-xs text-slate-400">Requested {formatDate(amendment.createdAt)}</p>
                      </div>
                      <Badge tone={amendmentStatusTone[amendment.status]}>{amendmentStatusLabel[amendment.status]}</Badge>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-2">
                      {amendment.status === "REQUESTED" && (
                        <Button size="sm" variant="outline" onClick={() => openClassifyModal(amendment)}>
                          Classify
                        </Button>
                      )}
                      {amendment.status === "CLASSIFIED" && amendment.amendmentType === "ADDENDUM" && (
                        <Button size="sm" variant="outline" onClick={() => openProposeGuarantorModal(amendment)}>
                          Propose guarantor
                        </Button>
                      )}
                      {amendment.status === "CLASSIFIED" && amendment.amendmentType !== "ADDENDUM" && (
                        <Button size="sm" variant="outline" onClick={() => openProposeTermsModal(amendment)}>
                          Propose terms
                        </Button>
                      )}
                      {amendment.status === "APPROVALS_PENDING" && (
                        <Button size="sm" variant="primary" loading={approvingId === amendment.id} onClick={() => approveAmendment(amendment)}>
                          Approve
                        </Button>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      )}

      <Modal open={Boolean(classifyTarget)} onClose={() => setClassifyTarget(null)} title="Classify amendment">
        <div className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Amendment type</label>
            <select
              value={amendmentType}
              onChange={(e) => setAmendmentType(e.target.value as AmendmentType)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              {AMENDMENT_TYPES.map((type) => (
                <option key={type} value={type}>
                  {amendmentTypeLabel[type]}
                </option>
              ))}
            </select>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setClassifyTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={classifying} onClick={submitClassify}>
              Classify
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(proposeTermsTarget)} onClose={() => setProposeTermsTarget(null)} title="Propose new terms">
        <div className="space-y-3.5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Monthly rent</label>
              <input
                type="number"
                value={monthlyRent}
                onChange={(e) => setMonthlyRent(e.target.value)}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Deposit amount</label>
              <input
                type="number"
                value={depositAmount}
                onChange={(e) => setDepositAmount(e.target.value)}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Start date</label>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Term (months)</label>
              <input
                type="number"
                value={termMonths}
                onChange={(e) => setTermMonths(e.target.value)}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
          </div>
          <p className="text-xs text-slate-400">Leave any field blank to keep it unchanged.</p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setProposeTermsTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={proposingTerms} onClick={submitProposeTerms}>
              Propose terms
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(proposeGuarantorTarget)} onClose={() => setProposeGuarantorTarget(null)} title="Propose a guarantor">
        <div className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Legal name</label>
            <input
              value={guarantorName}
              onChange={(e) => setGuarantorName(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Contact email</label>
            <input
              type="email"
              value={guarantorEmail}
              onChange={(e) => setGuarantorEmail(e.target.value)}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setProposeGuarantorTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={proposingGuarantor} onClick={submitProposeGuarantor}>
              Propose guarantor
            </Button>
          </div>
        </div>
      </Modal>

      <Modal open={Boolean(consentTarget)} onClose={() => setConsentTarget(null)} title="Record guarantor consent">
        <div className="space-y-3.5">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            The guarantor has no login to sign in-app — attach a reference to their wet-ink signed evidence (e.g. a
            scanned form filename or document ID).
          </p>
          <div>
            <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Evidence reference</label>
            <input
              value={consentEvidenceRef}
              onChange={(e) => setConsentEvidenceRef(e.target.value)}
              placeholder="e.g. scan-of-signed-guarantor-form.pdf"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConsentTarget(null)}>
              Cancel
            </Button>
            <Button variant="primary" loading={recordingConsent} onClick={submitConsent}>
              Record consent
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
