"use client";

import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { Circle, CircleMarker, MapContainer, Marker, TileLayer, useMap, useMapEvents } from "react-leaflet";
import { GOOGLE_MAPS_MAP_ID, browserMapProvider, loadGoogleMaps, rasterTiles } from "@/lib/map-provider";

const markerIcon = L.icon({
  iconUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png",
  iconRetinaUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41],
  iconAnchor: [12, 41],
});

export interface Point {
  latitude: number;
  longitude: number;
}

interface MapProps {
  original: Point | null;
  marker: Point;
  adjustable: boolean;
  policyMeters?: number;
  onMove?: (p: Point) => void;
  height?: number;
  /** Starting zoom (default 18, building level). */
  zoom?: number;
}

/** True when a browser map is configured (otherwise the wizard relies on its
 *  text, nudge and coordinate controls alone). */
export function hasBrowserMap() {
  return browserMapProvider() !== "none";
}

/**
 * ZR-PROPERTY-VERIFY-001 Section 6.4 map. Shows the provider's original
 * result (a faint dot, never moved) and the marker the host confirms. In
 * adjust mode the marker can be dragged or placed by clicking; the policy
 * radius shows how far it can move before review is needed. Rendered with
 * Google Maps JavaScript API (primary) or Mapbox / HERE (secondary), each
 * with its own restricted browser key. Client-only -- load through
 * next/dynamic with ssr: false. The map is never the only way to confirm a
 * location (the wizard has text, nudge and coordinate controls).
 */
export function PropertyPinMap(props: MapProps) {
  const provider = browserMapProvider();
  if (provider === "google") return <GooglePinMap {...props} />;
  if (provider === "none") return null;
  return <RasterPinMap {...props} />;
}

function GooglePinMap({ original, marker, adjustable, policyMeters, onMove, height = 300, zoom = 18 }: MapProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<google.maps.Map | null>(null);
  // Advanced markers (google.maps.Marker is deprecated).
  const pin = useRef<google.maps.marker.AdvancedMarkerElement | null>(null);
  const originDot = useRef<google.maps.marker.AdvancedMarkerElement | null>(null);
  const markerLib = useRef<google.maps.MarkerLibrary | null>(null);
  const radius = useRef<google.maps.Circle | null>(null);
  const listeners = useRef<google.maps.MapsEventListener[]>([]);
  const moveRef = useRef(onMove);
  const [failed, setFailed] = useState(false);
  const [ready, setReady] = useState(false);
  useEffect(() => { moveRef.current = onMove; }, [onMove]);

  useEffect(() => {
    let cancelled = false;
    loadGoogleMaps().then(async (maps) => {
      const lib = (await maps.importLibrary("marker")) as google.maps.MarkerLibrary;
      if (cancelled || !container.current || map.current) return;
      markerLib.current = lib;
      map.current = new maps.Map(container.current, {
        center: { lat: marker.latitude, lng: marker.longitude }, zoom, mapTypeId: "hybrid", mapId: GOOGLE_MAPS_MAP_ID,
        streetViewControl: false, fullscreenControl: false, clickableIcons: false,
      });
      pin.current = new lib.AdvancedMarkerElement({ map: map.current, position: { lat: marker.latitude, lng: marker.longitude },
        title: "Property marker" });
      setReady(true); // draw the overlays now that the map exists
    }).catch(() => setFailed(true));
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the overlays in step with props.
  useEffect(() => {
    const m = map.current;
    const lib = markerLib.current;
    const marker_ = pin.current;
    if (!ready || !m || !marker_ || !lib) return;
    const position = { lat: marker.latitude, lng: marker.longitude };
    marker_.position = position;
    marker_.gmpDraggable = adjustable;
    if (!m.getBounds()?.contains(position)) m.panTo(position);

    listeners.current.forEach((l) => l.remove());
    listeners.current = [];
    if (adjustable) {
      listeners.current.push(marker_.addListener("dragend", () => {
        const at = marker_.position;
        if (!at) return;
        const lat = typeof at.lat === "function" ? at.lat() : at.lat;
        const lng = typeof at.lng === "function" ? at.lng() : at.lng;
        moveRef.current?.({ latitude: lat, longitude: lng });
      }));
      listeners.current.push(m.addListener("click", (e: google.maps.MapMouseEvent) => {
        if (e.latLng) moveRef.current?.({ latitude: e.latLng.lat(), longitude: e.latLng.lng() });
      }));
    }

    if (original) {
      const at = { lat: original.latitude, lng: original.longitude };
      if (!originDot.current) {
        // The provider's original result: a faint dot that never moves.
        const dot = document.createElement("div");
        dot.style.cssText = "width:12px;height:12px;border-radius:9999px;background:#94a3b8;border:2px solid #64748b;";
        dot.setAttribute("aria-hidden", "true");
        originDot.current = new lib.AdvancedMarkerElement({ map: m, position: at, content: dot, title: "Map result" });
      } else originDot.current.position = at;
      if (adjustable && policyMeters) {
        if (!radius.current) {
          radius.current = new google.maps.Circle({ map: m, center: at, radius: policyMeters, clickable: false,
            strokeColor: "#2563eb", strokeWeight: 1, fillOpacity: 0.05 });
        } else { radius.current.setCenter(at); radius.current.setRadius(policyMeters); radius.current.setMap(m); }
      } else radius.current?.setMap(null);
    } else {
      if (originDot.current) originDot.current.map = null;
      originDot.current = null;
      radius.current?.setMap(null);
    }
  });

  if (failed) {
    return <p className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
      The map couldn&apos;t load. Use the controls below to confirm or position the marker.</p>;
  }
  return <div ref={container} className="overflow-hidden rounded-xl ring-1 ring-slate-200 dark:ring-white/10"
              style={{ height }} role="application" aria-label="Property location map" />;
}

function Recenter({ point }: { point: Point }) {
  const map = useMap();
  useEffect(() => {
    if (!map.getBounds().contains([point.latitude, point.longitude])) map.panTo([point.latitude, point.longitude]);
  }, [map, point.latitude, point.longitude]);
  return null;
}

function ClickToPlace({ onPlace }: { onPlace: (p: Point) => void }) {
  useMapEvents({ click: (e) => onPlace({ latitude: e.latlng.lat, longitude: e.latlng.lng }) });
  return null;
}

function RasterPinMap({ original, marker, adjustable, policyMeters, onMove, height = 300 }: MapProps) {
  const tiles = rasterTiles(browserMapProvider());
  const handlers = useMemo(() => ({
    dragend(e: L.DragEndEvent) {
      const ll = (e.target as L.Marker).getLatLng();
      onMove?.({ latitude: ll.lat, longitude: ll.lng });
    },
  }), [onMove]);
  if (!tiles) return null;

  return (
    <div className="overflow-hidden rounded-xl ring-1 ring-slate-200 dark:ring-white/10" style={{ height }}>
      <MapContainer center={[marker.latitude, marker.longitude]} zoom={18} scrollWheelZoom={adjustable}
                    style={{ height: "100%", width: "100%" }} keyboard>
        <TileLayer attribution={tiles.attribution} url={tiles.url} maxZoom={tiles.maxZoom} />
        {original && (
          <>
            <CircleMarker center={[original.latitude, original.longitude]} radius={6}
                          pathOptions={{ color: "#64748b", fillColor: "#94a3b8", fillOpacity: 0.8, weight: 2 }} />
            {adjustable && policyMeters ? (
              <Circle center={[original.latitude, original.longitude]} radius={policyMeters}
                      pathOptions={{ color: "#2563eb", weight: 1, fillOpacity: 0.05, dashArray: "4 4" }} />
            ) : null}
          </>
        )}
        <Marker position={[marker.latitude, marker.longitude]} icon={markerIcon} draggable={adjustable}
                eventHandlers={handlers} keyboard title="Property marker" />
        {adjustable && onMove ? <ClickToPlace onPlace={onMove} /> : null}
        <Recenter point={marker} />
      </MapContainer>
    </div>
  );
}
