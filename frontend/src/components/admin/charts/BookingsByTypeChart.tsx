"use client";

import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { useTheme } from "@/hooks/useTheme";

export interface BookingsByTypePoint {
  type: string;
  value: number;
}

const colors: Record<string, string> = {
  "Private Rooms": "#0e2f73",
};
const fallbackColor = "#94a3b8";

export function BookingsByTypeChart({ data: bookingsByType }: { data: BookingsByTypePoint[] }) {
  const isDark = useTheme() === "dark";
  const total = bookingsByType.reduce((sum, d) => sum + d.value, 0);

  return (
    <div className="flex items-center gap-4">
      <div className="relative h-44 w-44 shrink-0">
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={bookingsByType}
              dataKey="value"
              nameKey="type"
              innerRadius={52}
              outerRadius={72}
              paddingAngle={3}
              animationDuration={900}
              stroke="none"
            >
              {bookingsByType.map((entry) => (
                <Cell key={entry.type} fill={colors[entry.type] ?? fallbackColor} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                borderRadius: 12,
                background: isDark ? "#0f172a" : "#ffffff",
                border: isDark ? "1px solid #1e293b" : "1px solid #eef2fa",
              }}
              labelStyle={{ color: isDark ? "#e2e8f0" : "#0f172a" }}
              itemStyle={{ color: isDark ? "#e2e8f0" : "#0f172a" }}
            />
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-heading text-xl font-extrabold text-primary-900 dark:text-white">{total}</span>
          <span className="text-[11px] text-slate-400 dark:text-slate-400">Bookings</span>
        </div>
      </div>

      <div className="space-y-2.5">
        {bookingsByType.map((d) => (
          <div key={d.type} className="flex items-center gap-2 text-sm">
            <span
              className="h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: colors[d.type] ?? fallbackColor }}
            />
            <span className="text-slate-600 dark:text-slate-300">{d.type}</span>
            <span className="font-semibold text-primary-900 dark:text-white">{d.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
