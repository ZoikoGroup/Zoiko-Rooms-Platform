import { ReviewsList } from "@/components/admin/ReviewsList";
import { apiFetch, requireSuperAdmin } from "@/lib/api";
import { Review } from "@/lib/types";

export default async function AdminReviewsPage() {
  await requireSuperAdmin();
  const reviews = await apiFetch<Review[]>("/api/reviews");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Reviews</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Monitor guest feedback across all your properties.</p>
      </div>
      <ReviewsList reviews={reviews} />
    </div>
  );
}
