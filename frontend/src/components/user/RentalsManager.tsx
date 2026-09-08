"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { CalendarClock, DoorOpen, Repeat, Search, Star } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { UserOccupancy } from "@/lib/types";
import { occupancyStatusLabel, occupancyStatusTone } from "@/lib/status";
import { formatDate, todayIsoDate } from "@/lib/utils";
import { errorMessage, listOccupancies, requestMoveOut, submitListingReview, submitSubletRequest } from "@/lib/user-api";
import { Card, EmptyState, Field, Toast, inputClass, useToast } from "@/components/user/ui";

export function RentalsManager() {
  const { toast, showToast } = useToast();
  const [occupancies, setOccupancies] = useState<UserOccupancy[]>([]);
  const [loading, setLoading] = useState(true);

  const [subletFor, setSubletFor] = useState<UserOccupancy | null>(null);
  const [proposedPartyId, setProposedPartyId] = useState("");
  const [evidenceRef, setEvidenceRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [reviewFor, setReviewFor] = useState<UserOccupancy | null>(null);
  const [reviewRating, setReviewRating] = useState(0);
  const [reviewComment, setReviewComment] = useState("");
  const [reviewedOccupancyIds, setReviewedOccupancyIds] = useState<Set<number>>(new Set());
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewError, setReviewError] = useState("");

  const [vacateFor, setVacateFor] = useState<UserOccupancy | null>(null);
  const [desiredMoveOutDate, setDesiredMoveOutDate] = useState("");
  const [vacateSubmitting, setVacateSubmitting] = useState(false);
  const [vacateError, setVacateError] = useState("");

  const load = useCallback(async () => {
    try {
      setOccupancies(await listOccupancies());
    } catch (err) {
      showToast(errorMessage(err, "Could not load your rentals."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  function openSublet(occupancy: UserOccupancy) {
    setSubletFor(occupancy);
    setProposedPartyId("");
    setEvidenceRef("");
    setError("");
  }

  async function handleSublet(e: FormEvent) {
    e.preventDefault();
    if (!subletFor) return;
    const partyId = Number(proposedPartyId);
    if (!Number.isInteger(partyId) || partyId <= 0) {
      setError("Enter the numeric party ID of the person who would take over the room.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await submitSubletRequest(subletFor.id, {
        proposedRenterPartyId: partyId,
        authorityEvidenceRef: evidenceRef.trim(),
      });
      setSubletFor(null);
      showToast("Sublet request submitted for admin review.");
    } catch (err) {
      setError(errorMessage(err, "Could not submit the sublet request."));
    } finally {
      setSubmitting(false);
    }
  }

  function openVacate(occupancy: UserOccupancy) {
    setVacateFor(occupancy);
    setDesiredMoveOutDate("");
    setVacateError("");
  }

  async function handleVacate(e: FormEvent) {
    e.preventDefault();
    if (!vacateFor) return;
    if (!desiredMoveOutDate) {
      setVacateError("Pick the date you plan to move out.");
      return;
    }
    setVacateError("");
    setVacateSubmitting(true);
    try {
      const updated = await requestMoveOut(vacateFor.id, desiredMoveOutDate);
      setOccupancies((prev) => prev.map((o) => (o.id === updated.id ? updated : o)));
      setVacateFor(null);
      showToast("Move-out request sent to your host.");
    } catch (err) {
      setVacateError(errorMessage(err, "Could not submit your move-out request."));
    } finally {
      setVacateSubmitting(false);
    }
  }

  function openReview(occupancy: UserOccupancy) {
    setReviewFor(occupancy);
    setReviewRating(0);
    setReviewComment("");
    setReviewError("");
  }

  async function handleReviewSubmit(e: FormEvent) {
    e.preventDefault();
    if (!reviewFor || reviewRating < 1) {
      setReviewError("Pick a star rating.");
      return;
    }
    setReviewError("");
    setReviewSubmitting(true);
    try {
      await submitListingReview({ listingId: reviewFor.listingId, rating: reviewRating, comment: reviewComment.trim() });
      setReviewedOccupancyIds((prev) => new Set(prev).add(reviewFor.id));
      setReviewFor(null);
      showToast("Thanks — your review has been submitted.");
    } catch (err) {
      // Already reviewed this listing in a previous session -- treat as done, not an error.
      if (err instanceof Error && err.message.includes("already reviewed")) {
        setReviewedOccupancyIds((prev) => new Set(prev).add(reviewFor.id));
        setReviewFor(null);
        showToast("You've already reviewed this stay.");
      } else {
        setReviewError(errorMessage(err, "Could not submit your review."));
      }
    } finally {
      setReviewSubmitting(false);
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
                {occupancy.listingName || occupancy.listingId}
              </p>
              <Badge tone={occupancyStatusTone[occupancy.status] ?? "neutral"}>
                {occupancyStatusLabel[occupancy.status] ?? occupancy.status}
              </Badge>
            </div>
            <p className="mt-0.5 text-xs text-slate-400">
              {occupancy.propertyAddress || `Room #${occupancy.roomId}`}
              {occupancy.propertyCity ? `, ${occupancy.propertyCity}` : ""}
            </p>
            {occupancy.hostName && (
              <p className="mt-0.5 text-xs text-slate-400">Hosted by {occupancy.hostName}</p>
            )}

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
              {occupancy.requestedMoveOutDate && (
                <p className="flex items-center gap-1.5 font-medium text-amber-600 dark:text-amber-400">
                  <DoorOpen className="h-3.5 w-3.5" /> Move-out requested for {formatDate(occupancy.requestedMoveOutDate)}
                </p>
              )}
            </div>

            {occupancy.status === "ACTIVE" && (
              <div className="mt-4 space-y-2">
                <Button size="sm" variant="outline" className="w-full" onClick={() => openSublet(occupancy)}>
                  <Repeat className="h-3.5 w-3.5" /> Request to sublet
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="w-full"
                  disabled={Boolean(occupancy.requestedMoveOutDate)}
                  onClick={() => openVacate(occupancy)}
                >
                  <DoorOpen className="h-3.5 w-3.5" />
                  {occupancy.requestedMoveOutDate ? "Move-out requested" : "Request to vacate"}
                </Button>
              </div>
            )}
            {occupancy.status === "ENDED" && (
              <Button
                size="sm"
                variant={reviewedOccupancyIds.has(occupancy.id) ? "outline" : "primary"}
                className="mt-4 w-full"
                disabled={reviewedOccupancyIds.has(occupancy.id)}
                onClick={() => openReview(occupancy)}
              >
                <Star className="h-3.5 w-3.5" />
                {reviewedOccupancyIds.has(occupancy.id) ? "Reviewed" : "Leave a review"}
              </Button>
            )}
          </Card>
        ))}
      </div>

      <Modal open={Boolean(subletFor)} onClose={() => setSubletFor(null)} title="Request to sublet">
        <form onSubmit={handleSublet} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Zoiko has to approve every sublet. The person taking over the room must already have a Zoiko account
            with a verified identity — ask them for their party ID from their profile page.
          </p>

          <Field label="Proposed renter party ID" hint="Shown on the incoming renter's profile page.">
            <input
              inputMode="numeric"
              value={proposedPartyId}
              onChange={(e) => setProposedPartyId(e.target.value)}
              placeholder="e.g. 42"
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
            <Button type="button" variant="ghost" onClick={() => setSubletFor(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              Submit request
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(vacateFor)} onClose={() => setVacateFor(null)} title="Request to vacate">
        <form onSubmit={handleVacate} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            This sends your host a heads-up that you plan to move out. They&apos;ll follow up about final inspection and
            your deposit — this doesn&apos;t end your tenancy automatically.
          </p>

          <Field label="Desired move-out date">
            <input
              type="date"
              min={todayIsoDate()}
              value={desiredMoveOutDate}
              onChange={(e) => setDesiredMoveOutDate(e.target.value)}
              className={inputClass}
            />
          </Field>

          {vacateError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {vacateError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setVacateFor(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={vacateSubmitting}>
              Send request
            </Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(reviewFor)} onClose={() => setReviewFor(null)} title="Leave a review">
        <form onSubmit={handleReviewSubmit} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Your review helps future renters and updates this room&apos;s public rating.
          </p>

          <Field label="Rating">
            <div className="flex items-center gap-1">
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setReviewRating(n)}
                  aria-label={`${n} star${n === 1 ? "" : "s"}`}
                  className="p-0.5"
                >
                  <Star
                    className={`h-6 w-6 ${
                      n <= reviewRating
                        ? "fill-accent-500 text-accent-500"
                        : "fill-slate-200 text-slate-200 dark:fill-slate-700 dark:text-slate-700"
                    }`}
                  />
                </button>
              ))}
            </div>
          </Field>

          <Field label="Comment (optional)">
            <textarea
              value={reviewComment}
              onChange={(e) => setReviewComment(e.target.value)}
              rows={4}
              placeholder="How was your stay?"
              className={inputClass}
            />
          </Field>

          {reviewError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {reviewError}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setReviewFor(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={reviewSubmitting}>
              Submit review
            </Button>
          </div>
        </form>
      </Modal>

      <Toast toast={toast} />
    </div>
  );
}
