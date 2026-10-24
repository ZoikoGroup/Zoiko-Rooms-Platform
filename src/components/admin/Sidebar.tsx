"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BedDouble,
  CalendarRange,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  CreditCard,
  DoorOpen,
  Gavel,
  LayoutDashboard,
  LifeBuoy,
  LogOut,
  Settings,
  ShieldCheck,
  Star,
  Users,
  UsersRound,
  Wallet,
  X,
} from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { cn } from "@/lib/utils";
import { logout } from "@/lib/auth";

const navItems = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/bookings", label: "Bookings", icon: CalendarRange },
  { href: "/properties", label: "Properties & Rooms", icon: BedDouble },
  { href: "/leasing", label: "Leasing", icon: ClipboardList },
  { href: "/occupancy", label: "Occupancy", icon: DoorOpen },
  { href: "/disputes", label: "Disputes", icon: Gavel },
  { href: "/finance", label: "Finance", icon: Wallet },
  { href: "/guests", label: "Guests", icon: Users },
  { href: "/payments", label: "Payments", icon: CreditCard },
  { href: "/reviews", label: "Reviews", icon: Star },
  { href: "/trust-safety", label: "Trust & Safety", icon: ShieldCheck },
  { href: "/support", label: "Support", icon: LifeBuoy },
  { href: "/team", label: "Team", icon: UsersRound },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function Sidebar({
  collapsed,
  onToggleCollapse,
  mobileOpen,
  onCloseMobile,
}: {
  collapsed: boolean;
  onToggleCollapse: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    await logout();
    router.push("/login");
    router.refresh();
  }

  return (
    <>
      {mobileOpen && (
        <button
          aria-label="Close sidebar"
          onClick={onCloseMobile}
          className="fixed inset-0 z-40 bg-primary-900/50 backdrop-blur-sm lg:hidden"
        />
      )}

      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex flex-col bg-primary-900 text-primary-100 transition-all duration-300 ease-in-out lg:sticky lg:top-0 lg:h-screen lg:translate-x-0",
          collapsed ? "w-72 lg:w-[76px]" : "w-72",
          mobileOpen ? "translate-x-0" : "-translate-x-full"
        )}
      >
        <div className="bg-noise pointer-events-none absolute inset-0 opacity-20" />

        <div className="relative flex items-center justify-between px-5 py-5">
          <div className={cn(collapsed && "lg:hidden")}>
            <Logo variant="light" imgClassName="h-8 w-auto object-contain" />
          </div>
          {collapsed && (
            <span className="mx-auto hidden h-9 w-9 items-center justify-center rounded-xl bg-primary-700 font-heading text-sm font-extrabold text-white lg:flex">
              Z
            </span>
          )}
          <button onClick={onCloseMobile} className="text-primary-200 hover:text-white lg:hidden">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="relative hidden px-3 lg:block">
          <button
            onClick={onToggleCollapse}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className={cn(
              "flex w-full items-center gap-2.5 rounded-xl px-3.5 py-2.5 text-xs font-semibold text-primary-300 transition-colors hover:bg-white/10 hover:text-white",
              collapsed && "justify-center"
            )}
          >
            {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
            <span className={cn(collapsed && "hidden")}>Collapse</span>
          </button>
        </div>

        <nav className="relative mt-2 flex-1 space-y-1 overflow-y-auto px-3">
          {navItems.map((item) => {
            const Icon = item.icon;
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                title={collapsed ? item.label : undefined}
                className={cn(
                  "group relative flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-all duration-200",
                  active
                    ? "bg-white text-primary-800 shadow-lg shadow-primary-900/30"
                    : "text-primary-200 hover:bg-white/10 hover:text-white",
                  collapsed && "lg:justify-center"
                )}
              >
                <Icon className={cn("h-[18px] w-[18px] shrink-0 transition-transform duration-200", !active && "group-hover:scale-110")} />
                <span className={cn(collapsed && "lg:hidden")}>{item.label}</span>
                {active && (
                  <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-accent-600" />
                )}
              </Link>
            );
          })}
        </nav>

        <div className="relative border-t border-white/10 p-3">
          <button
            onClick={handleLogout}
            className={cn(
              "flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-primary-200 transition-colors hover:bg-accent-600/90 hover:text-white",
              collapsed && "lg:justify-center"
            )}
          >
            <LogOut className="h-[18px] w-[18px] shrink-0" />
            <span className={cn(collapsed && "lg:hidden")}>Logout</span>
          </button>
        </div>
      </aside>
    </>
  );
}
