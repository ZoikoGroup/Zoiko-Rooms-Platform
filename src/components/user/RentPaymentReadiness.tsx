"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/Badge";
import { PaymentConnection } from "@/lib/types";
import { getHostedRoomPaymentConnection } from "@/lib/user-api";

// What each payment-connection state means for the host, and what to do next.
const STATES: Record<string, { label: string; tone: "success" | "warning" | "danger" | "neutral"; next?: string }> = {
  ACTIVE: { label: "Ready to receive rent", tone: "success" },
  RECIPIENT_SETUP_REQUIRED: { label: "Add payment instructions", tone: "warning", next: "Add your bank, UPI or cash details so renters know how to pay you." },
  PENDING_VERIFICATION: { label: "Payment setup pending", tone: "warning", next: "Confirm the emailed code for your payment instructions, or wait for your room authority to be verified." },
  DRAFT: { label: "Room authority not verified", tone: "neutral", next: "Renters can pay you once Zoiko verifies your authority for this room." },
  SUSPENDED: { label: "Payments suspended", tone: "danger", next: "Your room authority or payment instructions need attention -- contact support." },
};

/** Whether a host's room can take rent yet -- the room's verified listing
 *  authority plus active payment instructions (bank transfer, UPI or cash).
 *  Links to where the host fixes whatever is missing. */
export function RentPaymentReadiness({ roomId }: { roomId: number }) {
  const [connection, setConnection] = useState<PaymentConnection | null>(null);

  useEffect(() => {
    let cancelled = false;
    getHostedRoomPaymentConnection(roomId)
      .then((c) => !cancelled && setConnection(c))
      .catch(() => !cancelled && setConnection(null));
    return () => {
      cancelled = true;
    };
  }, [roomId]);

  if (!connection) return null;
  const state = STATES[connection.state] ?? { label: connection.state, tone: "neutral" as const };
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5" title={state.next}>
      <Badge tone={state.tone}>{state.label}</Badge>
      {connection.state === "RECIPIENT_SETUP_REQUIRED" && (
        <Link href="/account/host/payments" className="text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300">
          Set up
        </Link>
      )}
    </span>
  );
}
