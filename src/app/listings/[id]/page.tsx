import { Suspense } from "react";
import { ListingDetailView } from "@/components/user/ListingDetailView";
import { Loader } from "@/components/ui/Loader";

export const dynamic = "force-dynamic";

export default async function PublicListingPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return (
    <div className="min-h-screen bg-[#EDF1F8] p-4 dark:bg-slate-950 sm:p-6 lg:p-8">
      <div className="mx-auto max-w-5xl">
        <Suspense fallback={<Loader label="Loading listing..." />}>
          <ListingDetailView listingId={id} />
        </Suspense>
      </div>
    </div>
  );
}
