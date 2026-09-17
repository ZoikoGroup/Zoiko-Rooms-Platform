"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { CalendarClock, DoorOpen, Repeat, Search } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { SubletRenterLookup, UserOccupancy } from "@/lib/types";
import { occupancyStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";
import { errorMessage, listOccupancies, lookupSubletRenter, submitSubletRequest } from "@/lib/user-api";
import { Card, EmptyState, Field, Toast, inputClass, useToast } from "@/components/user/ui";

export function RentalsManager() {
  const { toast, showToast } = useToast();
  const [occupancies, setOccupancies] = useState<UserOccupancy[]>([]);
  const [loading, setLoading] = useState(true);

  const [subletFor, setSubletFor] = useState<UserOccupancy | null>(null);
  const [proposedEmail, setProposedEmail] = useState("");
  const [lookupResult, setLookupResult] = useState<SubletRenterLookup | null>(null);
  const [lookingUp, setLookingUp] = useState(false);
  const [evidenceRef, setEvidenceRef] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

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
    setProposedEmail("");
    setLookupResult(null);
    setEvidenceRef("");
    setError("");
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
    setError("");
    setSubmitting(true);
    try {
      await submitSubletRequest(subletFor.id, {
        proposedRenterPartyId: lookupResult.partyId,
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

            {occupancy.status === "ACTIVE" && (
              <Button size="sm" variant="outline" className="mt-4 w-full" onClick={() => openSublet(occupancy)}>
                <Repeat className="h-3.5 w-3.5" /> Request to sublet
              </Button>
            )}
          </Card>
        ))}
      </div>

      <Modal open={Boolean(subletFor)} onClose={() => setSubletFor(null)} title="Request to sublet">
        <form onSubmit={handleSublet} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Zoiko has to approve every sublet. The person taking over the room must already have a Zoiko account
            with a verified identity.
          </p>

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

      <Toast toast={toast} />
    </div>
  );
}
