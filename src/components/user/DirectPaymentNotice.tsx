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
// One request per page load, shared by every card that asks.
let capabilitiesRequest: Promise<PaymentCapabilities> | null = null;

export function usePaymentCapabilities(): PaymentCapabilities | null {
  const [capabilities, setCapabilities] = useState<PaymentCapabilities | null>(null);
  useEffect(() => {
    let cancelled = false;
    capabilitiesRequest ??= getPaymentCapabilities();
    capabilitiesRequest
      .then((result) => {
        if (!cancelled) setCapabilities(result);
      })
      .catch(() => {
        capabilitiesRequest = null;
        if (!cancelled) setCapabilities(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return capabilities;
}

/** Zoiko Rooms Payment Model -- the renter-facing rent payment wording. */
export const DIRECT_RENT_PAYMENT_WORDING =
  "Your rent is paid directly to your host/property owner using the payment method agreed with them. Zoiko does not collect or process your rent payment.";

/** ZR-PAY-CFG-001 14.1 approved rental payment notice, with the way to pay. */
export function DirectPaymentNotice({ compact = false }: { compact?: boolean }) {
  return (
    <span className={`text-xs text-slate-500 dark:text-slate-400 ${compact ? "" : "block"}`}>
      Your rent is paid directly to your host/property owner using the payment method agreed with them —{" "}
      <Link href="/account/rent-payments" className="font-semibold text-primary-700 dark:text-primary-300">
        view payment instructions
      </Link>
      . Zoiko does not collect or process your rent payment.
    </span>
  );
}
