"use client";

import { Clock, Loader2, MapPin, ShieldAlert } from "lucide-react";
import { ExternalCard } from "@/lib/external-search";
import { cn } from "@/lib/utils";

interface ExternalDiscoveryCardProps {
  card: ExternalCard;
  onRequestContact: (card: ExternalCard) => void;
  contactPending?: boolean;
}

function formatPrice(amountMinor: number | null, currency: string | null): string | null {
  if (amountMinor === null || amountMinor === undefined) return null;
  try {
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency: currency ?? "GBP",
      maximumFractionDigits: 0,
    }).format(amountMinor);
  } catch {
    return `£${amountMinor}`;
  }
}

function formatDiscovered(iso: string | null): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

/**
 * Safe masked external discovery card (ZR-AI-SEARCH-001 safe-card rules).
 *
 * Deliberately NO "visit website", "book externally", "call landlord" or
 * "email agent" actions, and no third-party photos. The single CTA asks Zoiko
 * Rooms to start the (consent-gated) provider contact flow.
 */
export function ExternalDiscoveryCard({
  card,
  onRequestContact,
  contactPending = false,
}: ExternalDiscoveryCardProps) {
  const location = [card.locationCity, card.locationRegion, card.locationCountry]
    .filter(Boolean)
    .join(", ");
  const price = formatPrice(card.rentMonthly, null);
  const discovered = formatDiscovered(card.lastSeenAt);
  const canRequestContact = card.opportunityId != null && !contactPending;

  return (
    <div
      role="article"
      aria-label="External room listing, not verified by Zoiko Rooms. Appears listed, availability not confirmed."
      className="animate-chat-fade-slide overflow-hidden rounded-2xl bg-white ring-1 ring-slate-200 dark:bg-white/5 dark:ring-white/10"
    >
      <div className="relative">
        <div className="flex h-24 w-full items-center justify-center bg-gradient-to-br from-slate-100 to-slate-200 dark:from-slate-800 dark:to-slate-700">
          <div className="flex flex-col items-center gap-1 text-slate-400 dark:text-slate-400">
            <MapPin aria-hidden="true" className="h-6 w-6" />
            <span className="text-[10px] font-medium tracking-wide">MASKED LISTING</span>
          </div>
        </div>
        <div
          className={cn(
            "absolute left-3 top-3 flex items-center gap-1 rounded-full px-2 py-1 text-[10px] font-bold uppercase tracking-wide",
            "bg-amber-100 text-amber-800 dark:bg-amber-500/20 dark:text-amber-300"
          )}
        >
          <ShieldAlert aria-hidden="true" className="h-3 w-3" />
          External · Not verified by Zoiko Rooms
        </div>
        {card.imagesPresent && (
          <div
            aria-label="Image hidden"
            className="absolute bottom-2 right-2 rounded-full bg-black/50 px-2 py-0.5 text-[10px] text-white"
          >
            Image hidden
          </div>
        )}
      </div>

      <div className="space-y-2 p-3.5">
        <p className="text-sm font-semibold text-slate-800 dark:text-slate-100">
          {card.title || "Potential room listing"}
        </p>
        {location && (
          <p className="flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
            <MapPin className="h-3 w-3 shrink-0" />
            <span>Approximate area: {location}</span>
          </p>
        )}

        <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-600 dark:text-slate-300">
          {price && (
            <span className="font-semibold text-primary-700 dark:text-primary-300">
              Advertised price: {price} per month
            </span>
          )}
          {card.roomType && <span>{card.roomType}</span>}
          {card.occupancy && <span>{card.occupancy}</span>}
          {card.amenities.slice(0, 3).map((amenity) => (
            <span key={amenity}>{amenity}</span>
          ))}
        </div>

        <p className="flex items-center gap-1.5 text-[11px] text-slate-400 dark:text-slate-500">
          <Clock className="h-3 w-3 shrink-0" />
          {discovered ? `Appears listed · discovered ${discovered}` : "Appears listed"}
          {" — availability not confirmed"}
        </p>

        <button
          type="button"
          onClick={() => onRequestContact(card)}
          disabled={!canRequestContact}
          className={cn(
            "mt-1 w-full rounded-xl bg-primary-700 px-3 py-2 text-xs font-semibold text-white transition-colors",
            "hover:bg-primary-800 disabled:opacity-50"
          )}
        >
          {contactPending ? (
            <span className="flex items-center justify-center gap-1.5">
              <Loader2 className="h-3 w-3 animate-spin" /> Requesting…
            </span>
          ) : (
            "Ask Zoiko Rooms to contact provider"
          )}
        </button>
        <p className="text-center text-[10px] leading-relaxed text-slate-400 dark:text-slate-500">
          Contact only starts with your consent and the provider&apos;s acceptance. This is not a Zoiko Rooms listing.
        </p>
      </div>
    </div>
  );
}