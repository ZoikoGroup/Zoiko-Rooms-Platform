"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { BadgeCheck } from "lucide-react";
import { errorMessage } from "@/lib/user-api";
import { claimExternalRoom } from "@/lib/external-search";

/** ZR-AI-SEARCH-001 Claim & List: a provider who accepted a renter's
 *  introduction claims the room. A DRAFT listing they own is created; the
 *  usual identity, property and authority checks and the Listing Fee apply
 *  before it can be published. */
export default function ClaimRoomPage({ searchParams }: { searchParams: Promise<{ token?: string }> }) {
  const { token } = use(searchParams);
  const [result, setResult] = useState<{ listingId: string } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token) {
      setError("This link is missing its token. Use the link from Zoiko Rooms' email.");
      return;
    }
    claimExternalRoom(token)
      .then((r) => setResult({ listingId: r.listingId }))
      .catch((err) => setError(errorMessage(err, "We couldn't claim this room.")));
  }, [token]);

  return (
    <div className="mx-auto max-w-xl space-y-4">
      <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Claim your room</h1>
      {!result && !error && <p className="text-sm text-slate-400" role="status">Claiming the room...</p>}
      {error && <p className="text-sm text-accent-700" role="alert">{error}</p>}
      {result && (
        <div className="space-y-3 rounded-2xl bg-white p-5 ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-800">
          <p className="flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-300">
            <BadgeCheck className="h-4 w-4" aria-hidden="true" /> The room is now a draft listing in your account.
          </p>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Add your own description and photos, complete the identity, property and authority checks, and pay the
            Listing Fee to publish it. Until then it isn&apos;t shown in search and stays unverified.
          </p>
          <Link href="/account/host/listings" className="inline-flex rounded-xl bg-primary-700 px-4 py-2 text-sm font-semibold text-white hover:bg-primary-800">
            Go to my listings
          </Link>
        </div>
      )}
    </div>
  );
}
