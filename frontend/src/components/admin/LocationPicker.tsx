"use client";

import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";
import { Search } from "lucide-react";
import L from "leaflet";
import { MapContainer, Marker, TileLayer, useMap, useMapEvents } from "react-leaflet";

// Leaflet's default marker icon references image files that don't resolve under
// Next.js's bundler, so the icon URLs must be set explicitly.
const markerIcon = L.icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

const DEFAULT_CENTER: [number, number] = [20.5937, 78.9629]; // India centroid

function ClickHandler({ onPick }: { onPick: (lat: number, lng: number) => void }) {
  useMapEvents({
    click(e) {
      onPick(e.latlng.lat, e.latlng.lng);
    },
  });
  return null;
}

function MapRefSetter({ mapRef }: { mapRef: React.MutableRefObject<L.Map | null> }) {
  const map = useMap();
  useEffect(() => {
    mapRef.current = map;
  }, [map, mapRef]);
  return null;
}

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
  const [position, setPosition] = useState<[number, number]>(
    latitude != null && longitude != null ? [latitude, longitude] : DEFAULT_CENTER
  );
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const mapRef = useRef<L.Map | null>(null);

  function handlePick(lat: number, lng: number) {
    setPosition([lat, lng]);
    onChange(lat, lng);
  }

  async function handleSearch() {
    const q = query.trim();
    if (!q) return;
    setSearching(true);
    setSearchError("");
    try {
      const res = await fetch(
        `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(q)}`
      );
      const results: Array<{ lat: string; lon: string; display_name: string }> = await res.json();
      if (!results.length) {
        setSearchError("No location found for that search");
        return;
      }
      const lat = Number(results[0].lat);
      const lng = Number(results[0].lon);
      handlePick(lat, lng);
      onAddressResolved?.(results[0].display_name);
      mapRef.current?.flyTo([lat, lng], 14);
    } catch {
      setSearchError("Location search failed — try clicking the map instead");
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
      <MapContainer center={position} zoom={latitude != null ? 12 : 5} style={{ height: "220px", width: "100%" }}>
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <ClickHandler onPick={handlePick} />
        <MapRefSetter mapRef={mapRef} />
        {latitude != null && longitude != null && (
          <Marker
            position={position}
            icon={markerIcon}
            draggable
            eventHandlers={{
              dragend: (e) => {
                const marker = e.target as L.Marker;
                const { lat, lng } = marker.getLatLng();
                handlePick(lat, lng);
              },
            }}
          />
        )}
      </MapContainer>
      <p className="bg-slate-50 px-3 py-1.5 text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">
        Search for an address, click the map to drop a pin, or drag the pin to fine-tune the location.
      </p>
    </div>
  );
}
