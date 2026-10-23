"use client";

import { useEffect, useState } from "react";
import { Star } from "lucide-react";
import { listMyReviews, submitReview, MyReview } from "@/lib/user-api";

function Stars({ value, onPick }: { value: number; onPick?: (n: number) => void }) {
  return (
    <div className="flex gap-1" role={onPick ? "radiogroup" : undefined} aria-label="Rating">
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          disabled={!onPick}
          onClick={() => onPick?.(n)}
          aria-label={`${n} star${n === 1 ? "" : "s"}`}
          className={onPick ? "cursor-pointer" : "cursor-default"}
        >
          <Star className={`w-6 h-6 ${n <= value ? "fill-amber-400 text-amber-400" : "text-[#C4CCDD]"}`} />
        </button>
      ))}
    </div>
  );
}

/** Lets a renter rate the room they've rented. One review per listing; the
 *  review goes live straight away -- there is no approval step. */
export function RentalReviewCard({
  listingId,
  listingName,
  onMessage,
}: {
  listingId: string;
  listingName: string;
  onMessage: (message: string) => void;
}) {
  const [mine, setMine] = useState<MyReview | null | undefined>(undefined);
  const [rating, setRating] = useState(0);
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    listMyReviews()
      .then((all) => live && setMine(all.find((r) => r.listingId === listingId) ?? null))
      .catch(() => live && setMine(null));
    return () => {
      live = false;
    };
  }, [listingId]);

  async function submit() {
    if (rating < 1) {
      onMessage("Choose a star rating first.");
      return;
    }
    setSaving(true);
    try {
      setMine(await submitReview({ listingId, rating, comment: comment.trim() }));
      onMessage("Thanks — your review is now on the listing.");
    } catch (e) {
      onMessage(e instanceof Error && e.message ? e.message : "Could not save your review.");
    } finally {
      setSaving(false);
    }
  }

  if (mine === undefined) return null;

  return (
    <div className="p-3.5 border border-[#E1E7F1] rounded-2xl flex flex-col gap-2">
      <span className="text-[11px] font-bold uppercase tracking-wider text-[#5B6886]">
        {mine ? "Your review" : "Review this room"}
      </span>
      {mine ? (
        <>
          <Stars value={mine.rating} />
          {mine.comment && <p className="text-sm text-[#0B1B3F]">{mine.comment}</p>}
          <p className="text-xs text-[#5B6886]">
            Shown on {listingName || "the listing"} · {mine.date}
          </p>
        </>
      ) : (
        <>
          <Stars value={rating} onPick={setRating} />
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            maxLength={1000}
            rows={3}
            placeholder="How was your stay? (optional)"
            className="w-full rounded-xl border border-[#E1E7F1] p-2.5 text-sm text-[#0B1B3F] outline-none focus:ring-2 focus:ring-primary-500"
          />
          <button
            type="button"
            onClick={submit}
            disabled={saving}
            className="self-start h-9 px-5 rounded-full bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold transition cursor-pointer disabled:opacity-60"
          >
            {saving ? "Saving…" : "Submit review"}
          </button>
        </>
      )}
    </div>
  );
}
