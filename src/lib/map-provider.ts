/**
 * ZR-PROPERTY-VERIFY-001 Sections 3-4: which map the browser renders.
 * Google Maps JavaScript API is primary; Mapbox and HERE raster maps are the
 * pre-qualified secondaries. Each uses its own restricted, browser-only key
 * (website/referrer + API restrictions) -- never the server credentials.
 * With none configured there is no map; the location step stays fully
 * usable through text, nudge and coordinate controls (Section 17).
 */

export type BrowserMapProvider = "google" | "mapbox" | "here" | "none";

export const GOOGLE_MAPS_BROWSER_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_BROWSER_KEY ?? "";
export const MAPBOX_BROWSER_TOKEN = process.env.NEXT_PUBLIC_MAPBOX_TOKEN ?? "";
export const HERE_BROWSER_KEY = process.env.NEXT_PUBLIC_HERE_API_KEY ?? "";

export function browserMapProvider(): BrowserMapProvider {
  if (GOOGLE_MAPS_BROWSER_KEY) return "google";
  if (MAPBOX_BROWSER_TOKEN) return "mapbox";
  if (HERE_BROWSER_KEY) return "here";
  return "none";
}

/** Raster tile layer for the Leaflet renderer (Mapbox / HERE only). */
export function rasterTiles(provider: BrowserMapProvider): { url: string; attribution: string; maxZoom: number } | null {
  if (provider === "mapbox") {
    return {
      url: `https://api.mapbox.com/styles/v1/mapbox/streets-v12/tiles/256/{z}/{x}/{y}@2x?access_token=${encodeURIComponent(MAPBOX_BROWSER_TOKEN)}`,
      attribution: '&copy; <a href="https://www.mapbox.com/about/maps/">Mapbox</a>',
      maxZoom: 20,
    };
  }
  if (provider === "here") {
    return {
      url: `https://maps.hereapi.com/v3/base/mc/{z}/{x}/{y}/png8?style=explore.day&size=256&apiKey=${encodeURIComponent(HERE_BROWSER_KEY)}`,
      attribution: "&copy; HERE",
      maxZoom: 20,
    };
  }
  return null;
}

let googleLoader: Promise<typeof google.maps> | null = null;

/** Loads the Maps JavaScript API once with the restricted browser key. */
export function loadGoogleMaps(): Promise<typeof google.maps> {
  if (typeof window === "undefined") return Promise.reject(new Error("Google Maps needs a browser"));
  if (window.google?.maps?.Map) return Promise.resolve(window.google.maps);
  if (!googleLoader) {
    googleLoader = new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(GOOGLE_MAPS_BROWSER_KEY)}&v=weekly&loading=async&callback=__zoikoMapsReady`;
      script.async = true;
      (window as unknown as Record<string, () => void>).__zoikoMapsReady = () => resolve(window.google.maps);
      script.onerror = () => { googleLoader = null; reject(new Error("Google Maps failed to load")); };
      document.head.appendChild(script);
    });
  }
  return googleLoader;
}
