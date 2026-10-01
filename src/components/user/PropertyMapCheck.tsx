"use client";

import dynamic from "next/dynamic";
import { CheckCircle2, ExternalLink, MapPin, TriangleAlert } from "lucide-react";
import { PropertyVerification } from "@/lib/types";

const PropertyLocationMap = dynamic(
  () => import("@/components/user/PropertyLocationMap").then((m) => m.PropertyLocationMap),
  { ssr: false, loading: () => <div className="h-[180px] animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" /> }
);

const STATUS_COPY: Record<string, { title: string; ok: boolean }> = {
  FOUND: { title: "Address found on the map", ok: true },
  NOT_FOUND: { title: "Address not found on the map", ok: false },
  IMPRECISE: { title: "Only the area was found — not the exact street or building", ok: false },
  COUNTRY_MISMATCH: { title: "Address is in a different country than the property's region", ok: false },
  UNAVAILABLE: { title: "Map check couldn't run — the Zoiko team will check manually", ok: false },
};

const PRECISION_LABEL: Record<string, string> = {
  HOUSE: "building level",
  STREET: "street level",
  LOCALITY: "area level",
  REGION: "region level",
};

/**
 * The map check of a property's address (backend services/geocoding.py).
 * Property verification only completes automatically when this says
 * "Address found on the map". Shared by the host's verification card and the
 * admin review list.
 */
export function PropertyMapCheck({ record, compact = false }: { record: PropertyVerification; compact?: boolean }) {
  if (!record.geocodeStatus) return null;
  const copy = STATUS_COPY[record.geocodeStatus] ?? { title: record.geocodeStatus, ok: false };
  const hasPin = record.geocodeLatitude != null && record.geocodeLongitude != null;

  return (
    <div className="space-y-2">
      <div className="flex items-start gap-2">
        {copy.ok ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" aria-hidden="true" />
        ) : (
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden="true" />
        )}
        <div className="min-w-0 text-xs">
          <p className={`font-semibold ${copy.ok ? "text-emerald-700 dark:text-emerald-300" : "text-amber-700 dark:text-amber-300"}`}>
            {copy.title}
            {copy.ok && record.geocodePrecision && ` (${PRECISION_LABEL[record.geocodePrecision] ?? record.geocodePrecision})`}
          </p>
          {record.geocodeFormattedAddress && (
            <p className="mt-0.5 flex items-start gap-1 text-slate-500 dark:text-slate-400">
              <MapPin className="mt-0.5 h-3 w-3 shrink-0" aria-hidden="true" /> {record.geocodeFormattedAddress}
            </p>
          )}
          {!copy.ok && record.geocodeDetail && <p className="mt-0.5 text-slate-500 dark:text-slate-400">{record.geocodeDetail}</p>}
          {!hasPin && record.geocodeQuery && (
            <p className="mt-0.5 text-slate-400">Searched for: {record.geocodeQuery}</p>
          )}
        </div>
      </div>
      {hasPin && !compact && (
        <PropertyLocationMap latitude={record.geocodeLatitude as number} longitude={record.geocodeLongitude as number} />
      )}
      {record.googleMapsUrl && (
        <a
          href={record.googleMapsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300"
        >
          <ExternalLink className="h-3 w-3" aria-hidden="true" /> {hasPin ? "Open in Google Maps" : "Search in Google Maps"}
        </a>
      )}
    </div>
  );
}
