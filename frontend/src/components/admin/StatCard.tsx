import { LucideIcon } from "lucide-react";
import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  change,
  trend = "up",
  icon: Icon,
  index = 0,
}: {
  label: string;
  value: string;
  // Omit when there's no real period-over-period comparison to show -- a fabricated
  // trend is worse than no trend.
  change?: string;
  trend?: "up" | "down";
  icon: LucideIcon;
  index?: number;
}) {
  return (
    <div
      className="animate-fade-up group rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 transition-all duration-300 hover:-translate-y-1 hover:shadow-lg dark:bg-slate-900 dark:ring-white/10"
      style={{ animationDelay: `${index * 0.07}s` }}
    >
      <div className="flex items-center justify-between">
        <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary-50 text-primary-700 transition-transform duration-300 group-hover:scale-110 group-hover:bg-primary-700 group-hover:text-white dark:bg-primary-500/10 dark:text-primary-300">
          <Icon className="h-5 w-5" />
        </span>
        {change && (
          <span
            className={cn(
              "flex items-center gap-0.5 rounded-full px-2 py-1 text-xs font-bold",
              trend === "up" ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-600 dark:bg-rose-500/10 dark:text-rose-300"
            )}
          >
            {trend === "up" ? <ArrowUpRight className="h-3 w-3" /> : <ArrowDownRight className="h-3 w-3" />}
            {change}
          </span>
        )}
      </div>
      <p className="mt-4 font-heading text-2xl font-extrabold text-primary-900 dark:text-white">{value}</p>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{label}</p>
    </div>
  );
}
