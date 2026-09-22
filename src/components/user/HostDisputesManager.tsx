"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader } from "@/components/ui/Loader";
import { DisputeCasesSection } from "@/components/user/DisputeCasePanel";
import { DisputeCaseRead } from "@/lib/types";
import { errorMessage, hostDisputes } from "@/lib/user-api";
import { SectionHeading, Toast, useToast } from "@/components/user/ui";

/** ZR-ENG-CLR-010: the host-facing mirror of the Dispute Resolution engine's
 *  renter UI (see RentalsManager.tsx). The backend's host_router is a
 *  byte-for-byte mirror of the renter router (party-scoped instead of
 *  guest-scoped), so this just points the same shared panel at
 *  `hostDisputes` instead of `renterDisputes`.
 *
 *  Unlike the renter side, a host has no single "current occupancy" context
 *  to embed this in -- there's no `listHostOccupancies` in user-api.ts, so
 *  opening a case here asks for the occupancy ID directly (see
 *  DisputeCasePanel's `allowOccupancyInput`). */
export function HostDisputesManager() {
  const [cases, setCases] = useState<DisputeCaseRead[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast, showToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCases(await hostDisputes.listCases());
    } catch (err) {
      showToast(errorMessage(err, "Could not load disputes."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  if (loading) return <Loader label="Loading disputes" />;

  return (
    <div className="space-y-5">
      <SectionHeading
        title="Disputes"
        subtitle="Claims raised against or by you on a tenancy -- evidence, messages, settlements and outcomes all live here."
      />

      <DisputeCasesSection
        client={hostDisputes}
        viewerRole="HOST"
        cases={cases}
        showToast={showToast}
        onChanged={load}
        allowOccupancyInput
        variant="page"
        emptyMessage="No disputes yet. They'll show up here when a claim is raised on one of your tenancies."
      />

      <Toast toast={toast} />
    </div>
  );
}
