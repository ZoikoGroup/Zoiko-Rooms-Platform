import { HostingRentalRecordsManager } from "@/components/user/HostingRentalRecordsManager";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export const metadata = {
  title: "Rental Records | Host Workspace | Zoiko Rooms",
  description: "Manage upcoming, active, and completed rentals, agreements, and milestones.",
};

export default function HostRentalRecordsPage() {
  return <HostingRentalRecordsManager />;
}
