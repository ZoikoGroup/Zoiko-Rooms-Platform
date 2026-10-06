"use client";

import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import { Circle, CircleMarker, MapContainer, Marker, TileLayer, useMap, useMapEvents } from "react-leaflet";
import { browserMapProvider, loadGoogleMaps, rasterTiles } from "@/lib/map-provider";

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

function GooglePinMap({ original, marker, adjustable, policyMeters, onMove, height = 300 }: MapProps) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<google.maps.Map | null>(null);
  const pin = useRef<google.maps.Marker | null>(null);
  const originDot = useRef<google.maps.Marker | null>(null);
  const radius = useRef<google.maps.Circle | null>(null);
  const listeners = useRef<google.maps.MapsEventListener[]>([]);
  const moveRef = useRef(onMove);
  const [failed, setFailed] = useState(false);
  useEffect(() => { moveRef.current = onMove; }, [onMove]);

  useEffect(() => {
    let cancelled = false;
    loadGoogleMaps().then((maps) => {
      if (cancelled || !container.current || map.current) return;
      map.current = new maps.Map(container.current, {
        center: { lat: marker.latitude, lng: marker.longitude }, zoom: 18, mapTypeId: "hybrid",
        streetViewControl: false, fullscreenControl: false, clickableIcons: false,
      });
      pin.current = new maps.Marker({ map: map.current, position: { lat: marker.latitude, lng: marker.longitude },
        title: "Property marker" });
    }).catch(() => setFailed(true));
    return () => { cancelled = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep the overlays in step with props.
  useEffect(() => {
    const m = map.current;
    if (!m || !pin.current || typeof google === "undefined") return;
    const position = { lat: marker.latitude, lng: marker.longitude };
    pin.current.setPosition(position);
    pin.current.setDraggable(adjustable);
    if (!m.getBounds()?.contains(position)) m.panTo(position);

    listeners.current.forEach((l) => l.remove());
    listeners.current = [];
    if (adjustable) {
      listeners.current.push(pin.current.addListener("dragend", (e: google.maps.MapMouseEvent) => {
        if (e.latLng) moveRef.current?.({ latitude: e.latLng.lat(), longitude: e.latLng.lng() });
      }));
      listeners.current.push(m.addListener("click", (e: google.maps.MapMouseEvent) => {
        if (e.latLng) moveRef.current?.({ latitude: e.latLng.lat(), longitude: e.latLng.lng() });
      }));
    }

    if (original) {
      const at = { lat: original.latitude, lng: original.longitude };
      if (!originDot.current) {
        originDot.current = new google.maps.Marker({ map: m, position: at, clickable: false, icon: {
          path: google.maps.SymbolPath.CIRCLE, scale: 6, fillColor: "#94a3b8", fillOpacity: 0.9,
          strokeColor: "#64748b", strokeWeight: 2 } });
      } else originDot.current.setPosition(at);
      if (adjustable && policyMeters) {
        if (!radius.current) {
          radius.current = new google.maps.Circle({ map: m, center: at, radius: policyMeters, clickable: false,
            strokeColor: "#2563eb", strokeWeight: 1, fillOpacity: 0.05 });
        } else { radius.current.setCenter(at); radius.current.setRadius(policyMeters); radius.current.setMap(m); }
      } else radius.current?.setMap(null);
    } else {
      originDot.current?.setMap(null);
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
