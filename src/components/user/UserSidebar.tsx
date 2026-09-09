"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BedDouble,
  Building2,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  CreditCard,
  DoorOpen,
  LayoutDashboard,
  LogOut,
  MessageCircle,
  Repeat,
  Search,
  ShieldCheck,
  UserCircle2,
  X,
} from "lucide-react";
import { Logo } from "@/components/ui/Logo";
import { cn } from "@/lib/utils";
import { userLogout } from "@/lib/user-auth";

const navGroups = [
  {
    label: "",
    items: [{ href: "/account", label: "Dashboard", icon: LayoutDashboard }],
  },
  {
    label: "RENT",
    items: [
      { href: "/account/rent", label: "Find a Room", icon: Search },
      { href: "/account/applications", label: "My Applications", icon: ClipboardList },
      { href: "/account/rentals", label: "My Rentals", icon: DoorOpen },
      { href: "/account/sublets", label: "Sublet Requests", icon: Repeat },
    ],
  },
  {
    label: "HOST",
    items: [
      { href: "/account/host", label: "My Properties", icon: Building2 },
      { href: "/account/host/listings", label: "My Listings", icon: BedDouble },
    ],
  },
  {
    label: "ACCOUNT",
    items: [
      { href: "/account/identity", label: "Identity Verification", icon: ShieldCheck },
      { href: "/account/payments", label: "Payments", icon: CreditCard },
      { href: "/account/profile", label: "Profile", icon: UserCircle2 },
    ],
  },
];

export function UserSidebar({
  collapsed,
  onToggleCollapse,
  mobileOpen,
  onCloseMobile,
  onOpenChat,
}: {
  collapsed: boolean;
  onToggleCollapse: () => void;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  /** Opens the existing USER chat panel (rendered by the account shell layout) --
   *  this sidebar never renders chat UI itself, just triggers it. */
  onOpenChat: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    await userLogout();
    router.push("/account/login");
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
            <Logo variant="light" href="/account" imgClassName="h-8 w-auto object-contain" />
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

        <nav className="relative mt-2 flex-1 space-y-4 overflow-y-auto px-3 pb-4">
          {navGroups.map((group) => (
            <div key={group.label || "root"} className="space-y-1">
              {group.label && (
                <p
                  className={cn(
                    "px-3.5 pt-2 text-[10px] font-bold uppercase tracking-widest text-primary-400",
                    collapsed && "lg:hidden"
                  )}
                >
                  {group.label}
                </p>
              )}
              {group.items.map((item) => {
                const Icon = item.icon;
                const active = pathname === item.href;
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    onClick={onCloseMobile}
                    title={collapsed ? item.label : undefined}
                    className={cn(
                      "group relative flex items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium transition-all duration-200",
                      active
                        ? "bg-white text-primary-800 shadow-lg shadow-primary-900/30"
                        : "text-primary-200 hover:bg-white/10 hover:text-white",
                      collapsed && "lg:justify-center"
                    )}
                  >
                    <Icon
                      className={cn(
                        "h-[18px] w-[18px] shrink-0 transition-transform duration-200",
                        !active && "group-hover:scale-110"
                      )}
                    />
                    <span className={cn(collapsed && "lg:hidden")}>{item.label}</span>
                    {active && (
                      <span className="absolute -left-3 top-1/2 h-5 w-1 -translate-y-1/2 rounded-r-full bg-accent-600" />
                    )}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="relative space-y-1 border-t border-white/10 p-3">
          <button
            onClick={() => {
              onOpenChat();
              onCloseMobile();
            }}
            title={collapsed ? "Ask Zoiko" : undefined}
            aria-label="Open Ask Zoiko AI assistant"
            className={cn(
              "flex w-full items-center gap-3 rounded-xl px-3.5 py-2.5 text-sm font-medium text-primary-200 transition-colors hover:bg-white/10 hover:text-white",
              collapsed && "lg:justify-center"
            )}
          >
            <MessageCircle className="h-[18px] w-[18px] shrink-0" />
            <span className={cn(collapsed && "lg:hidden")}>Ask Zoiko</span>
          </button>
          <button
            onClick={handleLogout}
            title={collapsed ? "Logout" : undefined}
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
