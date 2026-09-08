"use client";

import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useTheme } from "@/hooks/useTheme";

export interface OccupancyByCityPoint {
  city: string;
  occupancy: number;
}

export function OccupancyChart({ data: occupancyByCity }: { data: OccupancyByCityPoint[] }) {
  const isDark = useTheme() === "dark";

  return (
    <div className="h-64 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={occupancyByCity} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke={isDark ? "#1e293b" : "#eef2fa"} />
          <XAxis
            dataKey="city"
            axisLine={false}
            tickLine={false}
            tick={{ fill: isDark ? "#94a3b8" : "#64748b", fontSize: 12 }}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={{ fill: isDark ? "#94a3b8" : "#64748b", fontSize: 12 }}
            tickFormatter={(v) => `${v}%`}
            width={40}
          />
          <Tooltip
            formatter={(value) => `${value}% occupancy`}
            contentStyle={{
              borderRadius: 12,
              background: isDark ? "#0f172a" : "#ffffff",
              border: isDark ? "1px solid #334155" : "1px solid #fdecec",
              boxShadow: isDark ? "0 8px 24px rgba(0,0,0,0.4)" : "0 8px 24px rgba(216,11,11,0.1)",
            }}
            labelStyle={{ color: isDark ? "#e2e8f0" : "#0f172a" }}
            itemStyle={{ color: isDark ? "#e2e8f0" : "#0f172a" }}
          />
          <Bar dataKey="occupancy" fill="#d80b0b" radius={[8, 8, 0, 0]} animationDuration={900} maxBarSize={36} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
