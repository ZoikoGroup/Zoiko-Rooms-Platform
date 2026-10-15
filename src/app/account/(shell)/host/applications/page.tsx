import { Suspense } from "react";
import { HostApplicationsManager } from "@/components/user/HostApplicationsManager";
import { Loader } from "@/components/ui/Loader";

export default function HostApplicationsPage() {
  // HostApplicationsManager reads ?applicationId= (useSearchParams).
  return (
    <Suspense fallback={<Loader label="Loading applications..." />}>
      <HostApplicationsManager />
    </Suspense>
  );
}
