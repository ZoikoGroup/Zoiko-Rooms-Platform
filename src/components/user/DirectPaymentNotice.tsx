"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { PaymentCapabilities } from "@/lib/types";
import { getPaymentCapabilities } from "@/lib/user-api";

/**
 * Reads which payment actions exist from the server (ZR-PAY-CFG-001 9.1) --
 * the UI never infers payment capability itself. Rent/deposit collection is
 * always off for Zoiko Rooms, so until the answer arrives we assume off and
 * never show a "Pay Zoiko" button.
 */
export function usePaymentCapabilities(): PaymentCapabilities | null {
  const [capabilities, setCapabilities] = useState<PaymentCapabilities | null>(null);
  useEffect(() => {
    getPaymentCapabilities()
      .then(setCapabilities)
      .catch(() => setCapabilities(null));
  }, []);
  return capabilities;
}

/** ZR-PAY-CFG-001 14.1 approved rental payment notice, with the way to pay. */
export function DirectPaymentNotice({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`text-xs text-slate-500 dark:text-slate-400 ${compact ? "" : "block"}`}>
      Pay your landlord, agent or other authorized recipient directly —{" "}
      <Link href="/account/rent-payments" className="font-semibold text-primary-700 dark:text-primary-300">
        view payment instructions
      </Link>
      . Zoiko Rooms does not receive or hold these funds.
    </span>
  );
}
