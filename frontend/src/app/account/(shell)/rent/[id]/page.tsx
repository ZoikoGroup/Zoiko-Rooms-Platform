import { ListingDetailView } from "@/components/user/ListingDetailView";

export default async function ListingDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <ListingDetailView listingId={id} />;
}
