"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCurrency } from "@/lib/utils";
import { useTheme } from "@/hooks/useTheme";

export interface RevenueTrendPoint {
  month: string;
  revenue: number;
  bookings: number;
}

export function RevenueChart({ data: revenueTrend }: { data: RevenueTrendPoint[] }) {
  const isDark = useTheme() === "dark";

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={revenueTrend} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#0e2f73" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#0e2f73" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid vertical={false} stroke={isDark ? "#1e293b" : "#eef2fa"} />
          <XAxis
            dataKey="month"
            axisLine={false}
            tickLine={false}
            tick={{ fill: isDark ? "#94a3b8" : "#64748b", fontSize: 12 }}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            tick={{ fill: isDark ? "#94a3b8" : "#64748b", fontSize: 12 }}
            tickFormatter={(v) => `₹${v / 1000}k`}
            width={48}
          />
          <Tooltip
            formatter={(value) => formatCurrency(Number(value))}
            contentStyle={{
              borderRadius: 12,
              background: isDark ? "#0f172a" : "#ffffff",
              border: isDark ? "1px solid #1e293b" : "1px solid #eef2fa",
              boxShadow: isDark ? "0 8px 24px rgba(0,0,0,0.4)" : "0 8px 24px rgba(14,47,115,0.12)",
            }}
            labelStyle={{ color: isDark ? "#e2e8f0" : "#0f172a" }}
            itemStyle={{ color: isDark ? "#e2e8f0" : "#0f172a" }}
          />
          <Area
            type="monotone"
            dataKey="revenue"
            stroke="#0e2f73"
            strokeWidth={2.5}
            fill="url(#revenueFill)"
            animationDuration={900}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
