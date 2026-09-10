"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { Building2, CalendarClock, DoorOpen, Repeat, Search, TrendingUp } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { BookingChangeRequest, PublicListing, SubletArrangementType, SubletRenterLookup, UserOccupancy } from "@/lib/types";
import {
  bookingChangeRequestStatusTone,
  bookingChangeTypeLabel,
  CO_TENANCY_ARRANGEMENT_TYPES,
  occupancyStatusTone,
  subletArrangementTypeLabel,
} from "@/lib/status";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
  errorMessage,
  listMyChangeRequests,
  listOccupancies,
  listPublicListings,
  lookupSubletRenter,
  submitExtensionRequest,
  submitFinancialChangeRequest,
  submitPremisesChangeRequest,
  submitSubletRequest,
  withdrawChangeRequest,
} from "@/lib/user-api";
import { Card, EmptyState, Field, Toast, inputClass, useToast } from "@/components/user/ui";

export function RentalsManager() {
  const { toast, showToast } = useToast();
  const [occupancies, setOccupancies] = useState<UserOccupancy[]>([]);
  const [changeRequests, setChangeRequests] = useState<BookingChangeRequest[]>([]);
  const [loading, setLoading] = useState(true);

  const [subletFor, setSubletFor] = useState<UserOccupancy | null>(null);
  const [proposedEmail, setProposedEmail] = useState("");
  const [lookupResult, setLookupResult] = useState<SubletRenterLookup | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const [evidenceRef, setEvidenceRef] = useState("");
  const [arrangementType, setArrangementType] = useState<SubletArrangementType>("ASSIGNMENT_FULL");
  const [proposedMonthlyRent, setProposedMonthlyRent] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [extensionFor, setExtensionFor] = useState<UserOccupancy | null>(null);
  const [additionalMonths, setAdditionalMonths] = useState("1");
  const [extensionReason, setExtensionReason] = useState("");
  const [extensionSubmitting, setExtensionSubmitting] = useState(false);
  const [extensionError, setExtensionError] = useState("");
  const [withdrawingId, setWithdrawingId] = useState<number | null>(null);

  const [premisesFor, setPremisesFor] = useState<UserOccupancy | null>(null);
  const [availableListings, setAvailableListings] = useState<PublicListing[]>([]);
  const [listingsLoading, setListingsLoading] = useState(false);
  const [targetListingId, setTargetListingId] = useState("");
  const [premisesReason, setPremisesReason] = useState("");
  const [premisesSubmitting, setPremisesSubmitting] = useState(false);
  const [premisesError, setPremisesError] = useState("");

  const [financialFor, setFinancialFor] = useState<UserOccupancy | null>(null);
  const [financialProposedRent, setFinancialProposedRent] = useState("");
  const [financialReason, setFinancialReason] = useState("");
  const [financialSubmitting, setFinancialSubmitting] = useState(false);
  const [financialError, setFinancialError] = useState("");

  const load = useCallback(async () => {
    try {
      const [occupanciesData, changeRequestsData] = await Promise.all([listOccupancies(), listMyChangeRequests()]);
      setOccupancies(occupanciesData);
      setChangeRequests(changeRequestsData);
    } catch (err) {
      showToast(errorMessage(err, "Could not load your rentals."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  function pendingRequestFor(occupancy: UserOccupancy): BookingChangeRequest | undefined {
    if (!occupancy.agreementId) return undefined;
    return changeRequests.find((cr) => cr.agreementId === occupancy.agreementId && cr.status === "PENDING");
  }

  function openSublet(occupancy: UserOccupancy) {
    setSubletFor(occupancy);
    setProposedEmail("");
    setLookupResult(null);
    setEvidenceRef("");
    setArrangementType("ASSIGNMENT_FULL");
    setProposedMonthlyRent("");
    setError("");
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
    setFinancialError("");
    setFinancialSubmitting(true);
    try {
      await submitFinancialChangeRequest(financialFor.agreementId, {
        proposedMonthlyRent: rent, reason: financialReason.trim(),
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
    const rentIsRelevant = (CO_TENANCY_ARRANGEMENT_TYPES as readonly string[]).includes(arrangementType);
    const parsedRent = proposedMonthlyRent.trim() ? Number(proposedMonthlyRent) : undefined;
    if (rentIsRelevant && proposedMonthlyRent.trim() && (!Number.isFinite(parsedRent) || (parsedRent ?? 0) <= 0)) {
      setError("Proposed monthly rent must be a positive number.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await submitSubletRequest(subletFor.id, {
        proposedRenterPartyId: lookupResult.partyId,
        authorityEvidenceRef: evidenceRef.trim(),
        arrangementType,
        ...(rentIsRelevant && parsedRent ? { proposedMonthlyRent: parsedRent } : {}),
      });
      setSubletFor(null);
      showToast("Sublet request submitted for admin review.");
    } catch (err) {
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

            {(() => {
              const pending = pendingRequestFor(occupancy);
              if (pending) {
                return (
                  <div className="mt-4 space-y-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800/60">
                    <div className="flex items-center justify-between gap-2">
                      <Badge tone={bookingChangeRequestStatusTone[pending.status] ?? "neutral"}>
                        {bookingChangeTypeLabel[pending.changeType] ?? pending.changeType} pending
                      </Badge>
                    </div>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {pending.changeType === "PREMISES_CHANGE" ? (
                        `Move to "${pending.targetListingName || pending.targetListingId}"`
                      ) : pending.changeType === "FINANCIAL_CHANGE" ? (
                        `${formatCurrency(pending.originalMonthlyRent ?? 0)} → ${formatCurrency(pending.proposedMonthlyRent ?? 0)}/month`
                      ) : (
                        `+${pending.additionalTermMonths} month${pending.additionalTermMonths === 1 ? "" : "s"} — new end date ${pending.proposedEndDate ? formatDate(pending.proposedEndDate) : "—"}`
                      )}
                    </p>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="w-full"
                      loading={withdrawingId === pending.id}
                      onClick={() => handleWithdraw(pending.id)}
                    >
                      Withdraw request
                    </Button>
                  </div>
                );
              }
              if (occupancy.status !== "ACTIVE") return null;
              return (
                <div className="mt-4 flex flex-col gap-2">
                  <Button size="sm" variant="outline" onClick={() => openSublet(occupancy)}>
                    <Repeat className="h-3.5 w-3.5" /> Request to sublet
                  </Button>
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
                    </>
                  )}
                </div>
              );
            })()}
          </Card>
        ))}
      </div>

      <Modal open={Boolean(subletFor)} onClose={() => setSubletFor(null)} title="Request to sublet">
        <form onSubmit={handleSublet} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Zoiko has to approve every request. The other person must already have a Zoiko account with a
            verified identity.
          </p>

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
            <Button type="button" variant="ghost" onClick={() => setSubletFor(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting} disabled={!lookupResult?.found || !lookupResult.identityVerified}>
              Submit request
            </Button>
          </div>
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

          <Field label="Proposed monthly rent (₹)">
            <input
              type="number"
              min={0}
              step="0.01"
              value={financialProposedRent}
              onChange={(e) => setFinancialProposedRent(e.target.value)}
              className={inputClass}
            />
          </Field>

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

      <Toast toast={toast} />
    </div>
  );
}
