import { BedDouble } from "lucide-react";

export function Loader({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-16">
      <div className="relative flex h-16 w-16 items-center justify-center">
        <span className="absolute inset-0 rounded-full border-4 border-primary-100 dark:border-primary-500/20" />
        <span className="absolute inset-0 rounded-full border-4 border-transparent border-t-accent-600 animate-spin" />
        <BedDouble className="h-6 w-6 text-primary-700 dark:text-primary-300" />
      </div>
      <p className="text-sm font-medium text-slate-500 dark:text-slate-400">
        {label}
        <span className="inline-block w-4 animate-pulse">...</span>
      </p>
    </div>
  );
}

export function TopProgressBar() {
  return (
    <div className="fixed inset-x-0 top-0 z-[100] h-1 overflow-hidden bg-primary-100 dark:bg-primary-500/15">
      <div className="h-full w-1/3 rounded-full bg-gradient-to-r from-primary-600 to-accent-600 animate-progress-indeterminate" />
    </div>
  );
}
