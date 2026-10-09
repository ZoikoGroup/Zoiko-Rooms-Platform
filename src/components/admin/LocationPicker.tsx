"use client";

import { useState } from "react";
import { Search } from "lucide-react";
import { apiClientFetch } from "@/lib/api-client";
import { browserMapProvider } from "@/lib/map-provider";
import { PropertyPinMap } from "@/components/user/PropertyPinMap";

const DEFAULT_CENTER = { latitude: 20.5937, longitude: 78.9629 }; // India centroid

/**
 * Admin property pin picker. Search goes through the backend location
 * adapter (ZR-PROPERTY-VERIFY-001: Google Maps Platform primary, Mapbox /
 * HERE fallback); the map is the shared PropertyPinMap. Client-only -- load
 * through next/dynamic with ssr: false.
 */
export function LocationPicker({
  latitude,
  longitude,
  onChange,
  onAddressResolved,
}: {
  latitude: number | null;
  longitude: number | null;
  onChange: (lat: number, lng: number) => void;
  onAddressResolved?: (address: string) => void;
}) {
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const hasPin = latitude != null && longitude != null;
  const marker = hasPin ? { latitude, longitude } : DEFAULT_CENTER;

  async function handleSearch() {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearchError("");
    try {
      const r = await apiClientFetch<{ found: boolean; location?: { latitude: number; longitude: number }; formatted?: string }>(
        "/api/admin/location/geocode", { method: "POST", body: JSON.stringify({ query: q }) },
      );
      if (!r.found || !r.location) {
        setSearchError("No location found for that search");
        return;
      }
      onChange(r.location.latitude, r.location.longitude);
      if (r.formatted) onAddressResolved?.(r.formatted);
    } catch {
      setSearchError("Location search is unavailable -- place the pin on the map instead");
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="overflow-hidden rounded-xl ring-1 ring-slate-200 dark:ring-slate-700">
      <div className="flex gap-2 bg-slate-50 p-2 dark:bg-slate-800">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                handleSearch();
              }
            }}
            placeholder="Search for an address or place"
            className="w-full rounded-lg bg-white py-2 pl-8 pr-3 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
          />
        </div>
        <button
          type="button"
          onClick={handleSearch}
          disabled={searching}
          className="shrink-0 rounded-lg bg-primary-700 px-3 py-2 text-xs font-semibold text-white transition-colors hover:bg-primary-800 disabled:opacity-60"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </div>
      {searchError && <p className="bg-slate-50 px-3 pb-2 text-[11px] text-accent-600 dark:bg-slate-800">{searchError}</p>}
      {browserMapProvider() === "none" ? (
        <p className="bg-slate-50 px-3 py-3 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          {hasPin ? `Pin: ${latitude.toFixed(6)}, ${longitude.toFixed(6)}` : "No pin yet."} No browser map key is configured --
          use search to set the location.
        </p>
      ) : (
        <PropertyPinMap original={null} marker={marker} adjustable height={220}
                        onMove={(p) => onChange(p.latitude, p.longitude)} />
      )}
      <p className="bg-slate-50 px-3 py-1.5 text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">
        Search for an address, click the map to drop a pin, or drag the pin to fine-tune the location.
      </p>
    </div>
  );
}
