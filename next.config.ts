import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Lets multiple `next dev` instances run from this same checkout at once
  // (e.g. one per port for manual multi-account testing) -- Next.js's
  // dev-server lock is keyed per distDir, not per port, so without this a
  // second instance refuses to start with "Another next dev server is
  // already running." Set NEXT_DIST_DIR when starting a second instance;
  // omitted, this falls back to the normal ".next" directory.
  distDir: process.env.NEXT_DIST_DIR || ".next",
  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "images.unsplash.com",
      },
    ],
  },
};

export default nextConfig;
