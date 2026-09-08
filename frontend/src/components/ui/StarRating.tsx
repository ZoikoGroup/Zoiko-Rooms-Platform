import { Star } from "lucide-react";

// A listing with zero reviews has no real rating -- showing a star score for it
// misrepresents an unrated property as known-quality (not the market convention;
// Airbnb et al. show "New" instead). Pass reviewCount when it's known so this
// can render honestly; omit it only where no review count exists to check (e.g.
// a single already-submitted review, which is always real on its own).
export function StarRating({ rating, size = 14, reviewCount }: { rating: number; size?: number; reviewCount?: number }) {
  if (reviewCount === 0) {
    return (
      <span
        className="inline-flex items-center rounded-full bg-primary-50 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-primary-700 dark:bg-primary-500/10 dark:text-primary-300"
        style={{ fontSize: size < 13 ? 9 : 10 }}
      >
        New
      </span>
    );
  }
  return (
    <div className="inline-flex items-center gap-0.5">
      {Array.from({ length: 5 }).map((_, i) => {
        const filled = i + 1 <= Math.round(rating);
        return (
          <Star
            key={i}
            size={size}
            className={
              filled
                ? "fill-accent-500 text-accent-500"
                : "fill-slate-200 text-slate-200 dark:fill-slate-700 dark:text-slate-700"
            }
          />
        );
      })}
    </div>
  );
}
