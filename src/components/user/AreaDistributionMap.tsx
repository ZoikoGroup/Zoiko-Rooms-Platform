"use client";

import { useEffect, useRef, useState } from "react";
import { MapPin } from "lucide-react";
import { GOOGLE_MAPS_MAP_ID, browserMapProvider, loadGoogleMaps, onGoogleMapsAuthFailure } from "@/lib/map-provider";

export interface MapArea {
  name: string;
  count: number;
  /** Approximate centre of the area's rooms (public coordinates are already rounded server-side). */
  latitude: number;
  longitude: number;
}

/**
 * Find a Room "Area Distribution" on Google Maps: one round marker per area showing how many rooms are
 * there; clicking a marker filters the list to that area. Uses the browser-only Google Maps key already
 * configured for the property location steps (NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY). Only approximate,
 * publicly-rounded coordinates are ever drawn -- exact addresses stay private until later stages.
 */
export function AreaDistributionMap({
  areas,
  onSelect,
  height = 420,
}: {
  areas: MapArea[];
  onSelect: (areaName: string) => void;
  height?: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<google.maps.Map | null>(null);
  const markers = useRef<google.maps.marker.AdvancedMarkerElement[]>([]);
  const selectRef = useRef(onSelect);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => { selectRef.current = onSelect; }, [onSelect]);

  // Create the map once.
  useEffect(() => {
    if (browserMapProvider() !== "google") return;
    let cancelled = false;
    // Google rejected the key: drop the map (and Google's own error box with it) for our fallback message.
    const stopListening = onGoogleMapsAuthFailure(() => {
      markers.current.forEach((marker) => { marker.map = null; });
      markers.current = [];
      map.current = null;
      setReady(false);
      setFailed(true);
    });
    loadGoogleMaps()
      .then(async (maps) => {
        await maps.importLibrary("marker");
        if (cancelled || !container.current || map.current) return;
        map.current = new maps.Map(container.current, {
          center: { lat: 20, lng: 0 }, zoom: 2, mapId: GOOGLE_MAPS_MAP_ID,
          streetViewControl: false, fullscreenControl: true, mapTypeControl: false, clickableIcons: false,
        });
        setReady(true);
      })
      .catch(() => setFailed(true));
    return () => { cancelled = true; stopListening(); };
  }, []);

  // Redraw the area markers whenever the areas change, then zoom to fit them.
  useEffect(() => {
    const m = map.current;
    if (!ready || !m) return;
    markers.current.forEach((marker) => { marker.map = null; });
    markers.current = [];

    const bounds = new google.maps.LatLngBounds();
    for (const area of areas) {
      const size = Math.min(64, Math.max(40, 34 + area.count * 6));
      const el = document.createElement("button");
      el.type = "button";
      el.title = `${area.name} — ${area.count} room${area.count === 1 ? "" : "s"}. Click to filter.`;
      el.setAttribute("aria-label", `${area.name}, ${area.count} room${area.count === 1 ? "" : "s"}. Filter by this area`);
      el.style.cssText =
        `width:${size}px;height:${size}px;border-radius:9999px;border:4px solid #0E2F73;background:#fff;` +
        "color:#0E2F73;font:800 15px/1 system-ui,sans-serif;display:flex;flex-direction:column;align-items:center;" +
        "justify-content:center;cursor:pointer;box-shadow:0 2px 8px rgba(14,47,115,.35);";
      el.innerHTML = `<span>${area.count}</span><span style="font-size:8px;font-weight:700;color:#64748B;letter-spacing:.05em">ROOMS</span>`;

      const marker = new google.maps.marker.AdvancedMarkerElement({
        map: m, position: { lat: area.latitude, lng: area.longitude }, content: el, title: area.name,
      });
      marker.addListener("click", () => selectRef.current(area.name));
      markers.current.push(marker);
      bounds.extend({ lat: area.latitude, lng: area.longitude });
    }

    if (areas.length === 1) {
      m.setCenter({ lat: areas[0].latitude, lng: areas[0].longitude });
      m.setZoom(12);
    } else if (areas.length > 1) {
      m.fitBounds(bounds, 64);
    }
  }, [areas, ready]);

  if (browserMapProvider() !== "google" || failed) {
    return (
      <div className="flex flex-col items-center justify-center px-6 py-16 text-center" style={{ minHeight: height }}>
        <MapPin className="h-10 w-10 text-slate-400" />
        <p className="mt-2 text-sm font-bold text-slate-600 dark:text-slate-400">
          {failed ? "The map couldn’t be loaded right now." : "The map isn’t available on this site yet."}
        </p>
        <p className="mt-1 text-xs text-slate-500">Use the list below to browse rooms — the filters still work.</p>
      </div>
    );
  }

  return <div ref={container} style={{ height }} className="w-full" role="application" aria-label="Map of room areas" />;
}
