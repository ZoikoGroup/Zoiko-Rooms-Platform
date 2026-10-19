import { Suspense } from "react";
import { RentalPaymentsManager } from "@/components/user/RentalPaymentsManager";
import { Loader } from "@/components/ui/Loader";

export default function RentPaymentsPage() {
  return (
    <Suspense fallback={<Loader label="Loading payments..." />}>
      <RentalPaymentsManager />
    </Suspense>
  );
}
