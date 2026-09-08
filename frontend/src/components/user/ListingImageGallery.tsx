"use client";

import { useState } from "react";
import { BedDouble, ChevronLeft, ChevronRight } from "lucide-react";
import { resolveImageUrl } from "@/lib/utils";

/** Read-only image gallery for a listing detail view -- large main image with
 *  prev/next arrows plus clickable thumbnails. Reuses the same resolveImageUrl
 *  used everywhere else images are shown, and degrades gracefully for zero,
 *  one, or broken images rather than assuming images[0] is always valid. */
export function ListingImageGallery({ images, alt }: { images: string[]; alt: string }) {
  const [activeIndex, setActiveIndex] = useState(0);
  const [brokenIndexes, setBrokenIndexes] = useState<Set<number>>(new Set());

  const validCount = images.length;
  const safeIndex = Math.min(activeIndex, Math.max(0, validCount - 1));
  const activeSrc = validCount > 0 ? resolveImageUrl(images[safeIndex]) : undefined;
  const activeBroken = brokenIndexes.has(safeIndex);

  function markBroken(index: number) {
    setBrokenIndexes((prev) => new Set(prev).add(index));
  }

  function goTo(direction: "prev" | "next") {
    if (validCount === 0) return;
    setActiveIndex((prev) => {
      const next = direction === "next" ? prev + 1 : prev - 1;
      return (next + validCount) % validCount;
    });
  }

  if (validCount === 0 || activeBroken) {
    return (
      <div className="flex h-64 w-full items-center justify-center rounded-2xl bg-primary-50 sm:h-96 dark:bg-primary-500/10">
        <BedDouble className="h-12 w-12 text-primary-300" />
      </div>
    );
  }

  return (
    <div>
      <div className="relative h-64 w-full overflow-hidden rounded-2xl bg-slate-100 sm:h-96 dark:bg-slate-800">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={activeSrc}
          alt={alt}
          className="h-full w-full object-cover"
          onError={() => markBroken(safeIndex)}
        />
        {validCount > 1 && (
          <>
            <button
              type="button"
              onClick={() => goTo("prev")}
              aria-label="Previous photo"
              className="absolute left-3 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-primary-900 shadow-md transition-transform hover:scale-105 dark:bg-slate-900/90 dark:text-white"
            >
              <ChevronLeft className="h-5 w-5" />
            </button>
            <button
              type="button"
              onClick={() => goTo("next")}
              aria-label="Next photo"
              className="absolute right-3 top-1/2 flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-full bg-white/90 text-primary-900 shadow-md transition-transform hover:scale-105 dark:bg-slate-900/90 dark:text-white"
            >
              <ChevronRight className="h-5 w-5" />
            </button>
            <span className="absolute bottom-3 right-3 rounded-full bg-black/60 px-2.5 py-1 text-xs font-medium text-white">
              {safeIndex + 1} / {validCount}
            </span>
          </>
        )}
      </div>

      {validCount > 1 && (
        <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
          {images.map((src, i) =>
            brokenIndexes.has(i) ? null : (
              <button
                key={src + i}
                type="button"
                onClick={() => setActiveIndex(i)}
                className={`h-16 w-16 shrink-0 overflow-hidden rounded-lg ring-2 transition-opacity ${
                  i === safeIndex ? "ring-primary-600 opacity-100" : "ring-transparent opacity-70 hover:opacity-100"
                }`}
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={resolveImageUrl(src)}
                  alt=""
                  className="h-full w-full object-cover"
                  onError={() => markBroken(i)}
                />
              </button>
            )
          )}
        </div>
      )}
    </div>
  );
}
