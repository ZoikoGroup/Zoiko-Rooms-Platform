"use client";

import { useCallback, useEffect, useState } from "react";
import { Loader } from "@/components/ui/Loader";
import { DisputeCasesSection } from "@/components/user/DisputeCasePanel";
import { DisputeCaseRead } from "@/lib/types";
import { errorMessage, renterDisputes } from "@/lib/user-api";
import { SectionHeading, Toast, useToast } from "@/components/user/ui";

/** Renter-side mirror of HostDisputesManager: every case the renter is a party
 *  to, including ones the host opened and payment disputes that aren't anchored
 *  to an occupancy (which the per-rental drawer tab in RentalsManager can't show). */
export function RenterDisputesManager() {
  const [cases, setCases] = useState<DisputeCaseRead[]>([]);
  const [loading, setLoading] = useState(true);
  const { toast, showToast } = useToast();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setCases(await renterDisputes.listCases());
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
        subtitle="Claims raised by you or your host on a rental -- evidence, messages, settlements and outcomes all live here."
      />

      <DisputeCasesSection
        client={renterDisputes}
        viewerRole="RENTER"
        cases={cases}
        showToast={showToast}
        onChanged={load}
        allowOccupancyInput
        variant="page"
        emptyMessage="No disputes yet. They'll show up here when a claim is raised on one of your rentals."
      />

      <Toast toast={toast} />
    </div>
  );
}
