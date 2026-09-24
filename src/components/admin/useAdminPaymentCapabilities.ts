"use client";

import { useEffect, useState } from "react";
import { PaymentCapabilities } from "@/lib/types";
import { apiClientFetch } from "@/lib/api-client";

/**
 * ZR-PAY-CFG-001 9.1: the admin UI reads which payment actions exist from
 * the backend instead of assuming. Rental money never moves through Zoiko
 * Rooms, so collection, deposit custody, payouts and rental refunds are off;
 * until the answer arrives everything is treated as off.
 */
export function useAdminPaymentCapabilities(): PaymentCapabilities | null {
  const [capabilities, setCapabilities] = useState<PaymentCapabilities | null>(null);
  useEffect(() => {
    apiClientFetch<PaymentCapabilities>("/api/finance/capabilities")
      .then(setCapabilities)
      .catch(() => setCapabilities(null));
  }, []);
  return capabilities;
}
