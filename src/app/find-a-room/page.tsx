import { Suspense } from "react";
import { UserSessionProvider } from "@/components/user/UserSessionContext";
import { RentBrowser } from "@/components/user/RentBrowser";
import { Loader } from "@/components/ui/Loader";

export default function PublicFindARoomPage() {
  return (
    <div className="min-h-screen bg-[#EDF1F8] p-4 dark:bg-slate-950 sm:p-6 lg:p-8">
      <div className="mx-auto max-w-[1560px]">
        <UserSessionProvider user={null}>
          <Suspense fallback={<Loader label="Loading available rooms" />}>
            <RentBrowser />
          </Suspense>
        </UserSessionProvider>
      </div>
    </div>
  );
}
