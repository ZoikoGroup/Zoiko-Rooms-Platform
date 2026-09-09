"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import Image from "next/image";
import { getCurrentAdmin, logout } from "@/lib/auth";
import { apiClientFetch } from "@/lib/api-client";
import { AppNotification, SearchResult } from "@/lib/types";
import {
  ADMIN_NOTIFICATIONS_BASE,
  getUnreadNotificationCount,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  resolveNotificationHref,
} from "@/lib/notifications";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { Bell, BedDouble, CalendarRange, ChevronDown, Loader2, LogOut, Menu, Search, Settings, UserCircle2, Users } from "lucide-react";

const resultIcons: Record<SearchResult["type"], typeof BedDouble> = {
  listing: BedDouble,
  guest: Users,
  booking: CalendarRange,
};

const NOTIFICATION_POLL_MS = 45_000;

function relativeTime(iso: string): string {
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function Topbar({
  onOpenMobileSidebar,
  onToggleChat,
}: {
  onOpenMobileSidebar: () => void;
  onToggleChat: () => void;
}) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [notifOpen, setNotifOpen] = useState(false);
  const [profileOpen, setProfileOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const ref = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getCurrentAdmin().then((admin) => setEmail(admin?.email ?? ""));
  }, []);

  const refreshUnreadCount = useCallback(() => {
    getUnreadNotificationCount(ADMIN_NOTIFICATIONS_BASE)
      .then(({ count }) => setUnreadCount(count))
      .catch(() => undefined);
  }, []);

  // Persistent, backend-backed notifications: fetch the real unread count on
  // mount and poll it periodically (no websockets -- this app has no existing
  // real-time infrastructure, so periodic refetch is the consistent choice).
  useEffect(() => {
    refreshUnreadCount();
    const interval = setInterval(refreshUnreadCount, NOTIFICATION_POLL_MS);
    return () => clearInterval(interval);
  }, [refreshUnreadCount]);

  useEffect(() => {
    if (!notifOpen) return;
    listNotifications(ADMIN_NOTIFICATIONS_BASE)
      .then(setNotifications)
      .catch(() => setNotifications([]));
  }, [notifOpen]);

  async function handleOpenNotification(notification: AppNotification) {
    if (!notification.isRead) {
      setNotifications((prev) => prev.map((n) => (n.id === notification.id ? { ...n, isRead: true } : n)));
      setUnreadCount((prev) => Math.max(0, prev - 1));
      try {
        await markNotificationRead(ADMIN_NOTIFICATIONS_BASE, notification.id);
      } catch {
        refreshUnreadCount();
      }
    }
    setNotifOpen(false);
    const href = resolveNotificationHref(notification, "admin");
    if (href) router.push(href);
  }

  async function handleMarkAllRead() {
    const previous = notifications;
    setNotifications((prev) => prev.map((n) => ({ ...n, isRead: true })));
    setUnreadCount(0);
    try {
      await markAllNotificationsRead(ADMIN_NOTIFICATIONS_BASE);
    } catch {
      setNotifications(previous);
      refreshUnreadCount();
    }
  }

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setNotifOpen(false);
        setProfileOpen(false);
      }
      if (searchRef.current && !searchRef.current.contains(e.target as Node)) {
        setSearchOpen(false);
      }
    }
    document.addEventListener("click", onClick);
    return () => document.removeEventListener("click", onClick);
  }, []);

  useEffect(() => {
    const trimmed = searchQuery.trim();
    if (trimmed.length < 2) {
      setSearchResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    const timeout = setTimeout(() => {
      apiClientFetch<SearchResult[]>(`/api/search?q=${encodeURIComponent(trimmed)}`)
        .then((results) => {
          setSearchResults(results);
          setSearchOpen(true);
        })
        .catch(() => setSearchResults([]))
        .finally(() => setSearching(false));
    }, 300);
    return () => clearTimeout(timeout);
  }, [searchQuery]);

  function handleSelectResult(result: SearchResult) {
    setSearchOpen(false);
    setSearchQuery("");
    router.push(result.href);
  }

  async function handleLogout() {
    await logout();
    router.push("/login");
    router.refresh();
  }

  return (
    <header className="sticky top-0 z-30 flex items-center justify-between gap-4 border-b border-slate-100 bg-white/90 px-4 py-3.5 backdrop-blur-md sm:px-6 dark:border-white/10 dark:bg-slate-900/90">
      <div className="flex items-center gap-3">
        <button
          onClick={onOpenMobileSidebar}
          className="rounded-lg p-2 text-primary-800 hover:bg-primary-50 lg:hidden dark:text-primary-200 dark:hover:bg-white/10"
        >
          <Menu className="h-5 w-5" />
        </button>

        <div ref={searchRef} className="relative hidden sm:block">
          <div className="flex items-center gap-2 rounded-full bg-slate-100 px-4 py-2 dark:bg-white/5">
            {searching ? (
              <Loader2 className="h-4 w-4 shrink-0 animate-spin text-slate-400" />
            ) : (
              <Search className="h-4 w-4 shrink-0 text-slate-400" />
            )}
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              onFocus={() => searchResults.length > 0 && setSearchOpen(true)}
              placeholder="Search bookings, guests, properties..."
              className="w-56 bg-transparent text-sm text-slate-600 outline-none placeholder:text-slate-400 lg:w-72 dark:text-slate-200"
            />
          </div>

          {searchOpen && (
            <div className="animate-scale-in absolute left-0 top-full mt-2 w-full min-w-[320px] origin-top rounded-2xl bg-white p-2 shadow-xl shadow-primary-900/15 ring-1 ring-slate-100 dark:bg-slate-800 dark:shadow-black/40 dark:ring-white/10">
              {searchResults.length === 0 ? (
                <p className="px-3 py-4 text-center text-sm text-slate-400">
                  {searchQuery.trim().length < 2 ? "Keep typing to search..." : "No matches found."}
                </p>
              ) : (
                searchResults.map((result) => {
                  const Icon = resultIcons[result.type];
                  return (
                    <button
                      key={`${result.type}-${result.id}`}
                      onClick={() => handleSelectResult(result)}
                      className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors hover:bg-primary-50 dark:hover:bg-white/10"
                    >
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
                        <Icon className="h-4 w-4" />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-medium text-slate-700 dark:text-slate-200">
                          {result.title}
                        </span>
                        <span className="block truncate text-xs text-slate-400">{result.subtitle}</span>
                      </span>
                    </button>
                  );
                })
              )}
            </div>
          )}
        </div>
      </div>

      <div ref={ref} className="flex items-center gap-3">
        <ThemeToggle />

        <button
          onClick={onToggleChat}
          aria-label="Open Ask Zoiko AI assistant"
          title="Ask Zoiko · AI assistant"
          className="flex h-10 w-10 items-center justify-center rounded-full text-slate-500 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-white"
        >
          <Image
            src="/zoikorooms-icon-png.png"
            alt=""
            width={40}
            height={40}
            priority
            className="h-8 w-8 rounded-full object-cover"
          />
        </button>

        <div className="relative">
          <button
            onClick={() => {
              setNotifOpen((o) => !o);
              setProfileOpen(false);
            }}
            className="relative flex h-10 w-10 items-center justify-center rounded-full text-slate-500 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-white"
          >
            <Bell className="h-5 w-5" />
            {unreadCount > 0 && (
              <span className="absolute right-2 top-2 h-2 w-2 animate-pulse-ring rounded-full bg-accent-600" />
            )}
          </button>

          {notifOpen && (
            <div className="animate-scale-in absolute right-0 mt-2 w-80 origin-top-right rounded-2xl bg-white p-2 shadow-xl shadow-primary-900/15 ring-1 ring-slate-100 dark:bg-slate-800 dark:shadow-black/40 dark:ring-white/10">
              <div className="flex items-center justify-between px-3 py-2">
                <p className="text-xs font-bold uppercase tracking-wide text-slate-400">Notifications</p>
                {notifications.some((n) => !n.isRead) && (
                  <button
                    onClick={handleMarkAllRead}
                    className="text-xs font-semibold text-primary-700 hover:text-accent-600 dark:text-primary-300"
                  >
                    Mark all read
                  </button>
                )}
              </div>
              <div className="max-h-80 overflow-y-auto">
                {notifications.length === 0 && (
                  <p className="px-3 py-4 text-center text-sm text-slate-400">No notifications yet.</p>
                )}
                {notifications.map((n) => (
                  <button
                    key={n.id}
                    onClick={() => handleOpenNotification(n)}
                    className={`flex w-full items-start gap-2 rounded-xl px-3 py-2.5 text-left text-sm transition-colors hover:bg-primary-50 dark:hover:bg-white/10 ${
                      n.isRead ? "" : "bg-primary-50/60 dark:bg-primary-500/10"
                    }`}
                  >
                    {!n.isRead && <span className="mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full bg-accent-600" />}
                    <span className={n.isRead ? "ml-3.5" : ""}>
                      <span className="block font-medium text-slate-700 dark:text-slate-200">{n.title}</span>
                      {n.message && <span className="mt-0.5 block text-xs text-slate-500 dark:text-slate-400">{n.message}</span>}
                      <span className="mt-0.5 block text-xs text-slate-400">{relativeTime(n.createdAt)}</span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="relative">
          <button
            onClick={() => {
              setProfileOpen((o) => !o);
              setNotifOpen(false);
            }}
            className="flex items-center gap-2 rounded-full py-1 pl-1 pr-2.5 transition-colors hover:bg-primary-50 dark:hover:bg-white/10"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-full bg-primary-700 text-sm font-bold text-white">
              {email ? email[0].toUpperCase() : "A"}
            </span>
            <span className="hidden text-sm font-medium text-slate-600 sm:block dark:text-slate-300">
              {email || "Admin"}
            </span>
            <ChevronDown className="hidden h-4 w-4 text-slate-400 sm:block" />
          </button>

          {profileOpen && (
            <div className="animate-scale-in absolute right-0 mt-2 w-56 origin-top-right rounded-2xl bg-white p-2 shadow-xl shadow-primary-900/15 ring-1 ring-slate-100 dark:bg-slate-800 dark:shadow-black/40 dark:ring-white/10">
              <div className="px-3 py-2">
                <p className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{email || "admin"}</p>
                <p className="text-xs text-slate-400">Zoiko Rooms Admin</p>
              </div>
              <Link
                href="/settings"
                className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-slate-600 hover:bg-primary-50 dark:text-slate-300 dark:hover:bg-white/10"
              >
                <UserCircle2 className="h-4 w-4" /> Profile
              </Link>
              <Link
                href="/settings"
                className="flex items-center gap-2 rounded-xl px-3 py-2 text-sm text-slate-600 hover:bg-primary-50 dark:text-slate-300 dark:hover:bg-white/10"
              >
                <Settings className="h-4 w-4" /> Settings
              </Link>
              <button
                onClick={handleLogout}
                className="flex w-full items-center gap-2 rounded-xl px-3 py-2 text-sm text-accent-600 hover:bg-accent-50 dark:hover:bg-accent-500/10"
              >
                <LogOut className="h-4 w-4" /> Logout
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
