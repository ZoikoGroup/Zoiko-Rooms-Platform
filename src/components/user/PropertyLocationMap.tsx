"use client";

import { browserMapProvider } from "@/lib/map-provider";
import { PropertyPinMap } from "@/components/user/PropertyPinMap";

/** Read-only map pin for where a property's address resolved, on the
 *  configured map provider (Google primary, Mapbox / HERE secondary).
 *  Client-only -- load it through next/dynamic with ssr: false. */
export function PropertyLocationMap({ latitude, longitude, height = 180 }: { latitude: number; longitude: number; height?: number }) {
  if (browserMapProvider() === "none") return null;
  return <PropertyPinMap original={null} marker={{ latitude, longitude }} adjustable={false} height={height} />;
}
