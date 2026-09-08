import { UserSessionProvider } from "@/components/user/UserSessionContext";
import { RentBrowser } from "@/components/user/RentBrowser";
import { RoomAlertSignup } from "@/components/user/RoomAlertSignup";

export default function PublicFindARoomPage() {
  return (
    <div className="min-h-screen bg-slate-50 p-4 dark:bg-slate-950 sm:p-6 lg:p-8">
      <div className="mx-auto max-w-6xl space-y-6">
        <div>
          <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Find a Room</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Browse verified private rooms available for 30+ night stays. Sign in to view full details and apply.
          </p>
        </div>
        <UserSessionProvider user={null}>
          <RentBrowser />
        </UserSessionProvider>
        <RoomAlertSignup />
      </div>
    </div>
  );
}
