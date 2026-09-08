import { RentBrowser } from "@/components/user/RentBrowser";

export default function RentPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Find a Room</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Browse verified private rooms available for 30+ night stays and apply to the one you want.
        </p>
      </div>
      <RentBrowser />
    </div>
  );
}
