"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Send } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { PublicListing } from "@/lib/types";
import { formatCurrency, todayIsoDate } from "@/lib/utils";
import { errorMessage, submitRentalApplication } from "@/lib/user-api";
import { Field, inputClass } from "@/components/user/ui";

/** Shared by RentBrowser's listing grid and the listing detail page, so the
 *  apply flow (including move-in-date validation) only exists in one place. */
export function ApplyForRoomModal({
  listing,
  onClose,
  onApplied,
}: {
  listing: PublicListing | null;
  onClose: () => void;
  onApplied: (listingId: string) => void;
}) {
  const [message, setMessage] = useState("");
  const [desiredMoveIn, setDesiredMoveIn] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (listing) {
      setMessage("");
      setDesiredMoveIn("");
      setError("");
    }
  }, [listing]);

  async function handleApply(e: FormEvent) {
    e.preventDefault();
    if (!listing) return;
    if (desiredMoveIn && desiredMoveIn < todayIsoDate()) {
      setError("Desired move-in date cannot be in the past.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await submitRentalApplication({
        listingId: listing.id,
        message: message.trim(),
        desiredMoveIn: desiredMoveIn || null,
      });
      onApplied(listing.id);
    } catch (err) {
      setError(errorMessage(err, "Could not submit your application."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={Boolean(listing)} onClose={onClose} title={`Apply for ${listing?.name ?? ""}`}>
      <form onSubmit={handleApply} className="space-y-4">
        <div className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          <p>
            {listing?.location}, {listing?.city} — {listing ? formatCurrency(listing.pricePerNight, listing.currency) : ""} / night,
            minimum {listing?.minStayNights} nights.
          </p>
          <p className="mt-1">Host contact: {listing?.ownerName || "Zoiko host"}</p>
        </div>

        <Field label="Desired move-in date">
          <input
            type="date"
            value={desiredMoveIn}
            min={todayIsoDate()}
            onChange={(e) => setDesiredMoveIn(e.target.value)}
            className={inputClass}
          />
        </Field>

        <Field label="Message to the host" hint="Tell the host a little about yourself and your stay.">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={4}
            placeholder="I'm relocating for work and looking for a 6-month stay..."
            className={inputClass}
          />
        </Field>

        {error && (
          <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
            {error}
          </p>
        )}

        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" loading={submitting}>
            <Send className="h-4 w-4" /> Submit application
          </Button>
        </div>
      </form>
    </Modal>
  );
}
