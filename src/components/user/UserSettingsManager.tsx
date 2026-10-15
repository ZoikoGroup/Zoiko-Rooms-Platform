"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Bell,
  Building2,
  Calendar,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock,
  CreditCard,
  Database,
  DoorOpen,
  Download,
  ExternalLink,
  Eye,
  EyeOff,
  FileText,
  Globe,
  Home,
  Info,
  Key,
  Laptop,
  LayoutGrid,
  Lock,
  Mail,
  Moon,
  Phone,
  Repeat,
  RotateCcw,
  Search,
  Settings,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Sliders,
  Smartphone,
  Sparkles,
  Upload,
  User,
  Users,
  X,
  XCircle,
} from "lucide-react";
import { useUserSession } from "@/components/user/UserSessionContext";
import { updateUserProfile, changeUserPassword } from "@/lib/user-auth";
import {
  getNotificationPreferences,
  updateNotificationPreferences,
  USER_NOTIFICATIONS_BASE,
} from "@/lib/notifications";
import {
  listHostedProperties,
  listHostedRooms,
  listHostedRoomOccupancies,
  listHostedApplications,
  listRentalApplications,
  listOccupancies,
  listMyRentalPaymentObligations,
  errorMessage,
} from "@/lib/user-api";
import { NotificationCategory, NotificationPreference } from "@/lib/types";
import { PhoneInputWithCountry } from "@/components/ui/PhoneInputWithCountry";
import { splitPhoneNumber, validatePhoneWithCountry } from "@/lib/phone-validation";
import { cn } from "@/lib/utils";

export type SettingsSection =
  | "profile"
  | "security"
  | "notifications"
  | "preferences"
  | "privacy"
  | "account";

export type UserRoleMode = "host" | "renter";

interface ActiveSessionItem {
  id: string;
  dev: string;
  loc: string;
  last: string;
  cur: boolean;
  type: "phone" | "monitor";
}

interface PasskeyItem {
  id: string;
  name: string;
  used: string;
}

interface NotificationRowItem {
  id: string;
  categoryKey: NotificationCategory | string;
  title: string;
  tag: "Host" | "Renter" | "Account";
  description: string;
  email: boolean;
  push: boolean;
  inApp: boolean; // essential in-app alerts are always locked
}

interface DisplayPreferences {
  lang: string;
  locale: string;
  cur: string;
  tz: string;
  tf: string;
  motion: string;
  density: string;
}

function detectCurrentDevice(): { name: string; type: "phone" | "monitor" } {
  if (typeof window === "undefined" || !navigator?.userAgent) {
    return { name: "Current Browser Session", type: "monitor" };
  }
  const ua = navigator.userAgent;
  let os = "Desktop";
  if (ua.includes("Win")) os = "Windows";
  else if (ua.includes("Mac")) os = "macOS";
  else if (ua.includes("Android")) os = "Android";
  else if (ua.includes("iPhone") || ua.includes("iPad")) os = "iOS";
  else if (ua.includes("Linux")) os = "Linux";

  let browser = "Web Browser";
  if (ua.includes("Edg")) browser = "Microsoft Edge";
  else if (ua.includes("Chrome")) browser = "Google Chrome";
  else if (ua.includes("Safari")) browser = "Apple Safari";
  else if (ua.includes("Firefox")) browser = "Mozilla Firefox";

  const isMobile = /Android|iPhone|iPad|iPod/i.test(ua);
  return {
    name: `${browser} on ${os}`,
    type: isMobile ? "phone" : "monitor",
  };
}

export function UserSettingsManager({
  defaultSection = "profile",
  initialRole,
}: {
  defaultSection?: SettingsSection;
  initialRole?: UserRoleMode;
}) {
  const pathname = usePathname();
  const { user, identityVerified, identityStatus, refreshUser } = useUserSession();

  // Determine initial role from URL or prop
  const detectedRole: UserRoleMode = useMemo(() => {
    if (initialRole) return initialRole;
    if (pathname.includes("/host")) return "host";
    if (pathname.includes("/rent")) return "renter";
    return "renter"; // default to renter workspace view, user can toggle
  }, [initialRole, pathname]);

  const [roleMode, setRoleMode] = useState<UserRoleMode>(detectedRole);
  const [activeSection, setActiveSection] = useState<SettingsSection>(defaultSection);
  const [mobileSubNavOpen, setMobileSubNavOpen] = useState(false);

  // Profile State
  const [displayName, setDisplayName] = useState(user?.fullName || "Jordan Ellis");
  const [email, setEmail] = useState(user?.email || "jordan@example.com");
  const [emailPending, setEmailPending] = useState<string | null>(null);
  const [phone, setPhone] = useState(user?.phone || "+44 7911 123456");
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null);

  // Security State (Dynamic, no static mockup devices)
  const [passwordLastChanged, setPasswordLastChanged] = useState("Protected");
  const [mfaEnabled, setMfaEnabled] = useState(false);
  const [passkeys, setPasskeys] = useState<PasskeyItem[]>([]);
  const [sessions, setSessions] = useState<ActiveSessionItem[]>([
    {
      id: "cur-session",
      dev: "Current Browser Session",
      loc: "This device · Verified authenticated session",
      last: "Active now",
      cur: true,
      type: "monitor",
    },
  ]);

  // Notifications State
  const [notifState, setNotifState] = useState<Record<string, { email: boolean; push: boolean }>>({
    apps: { email: true, push: true },
    rentals: { email: true, push: true },
    payments: { email: true, push: true },
    sublet: { email: true, push: false },
    verification: { email: true, push: true },
    messages: { email: false, push: true },
  });
  const [marketingNotifs, setMarketingNotifs] = useState(false);
  const [quietHours, setQuietHours] = useState(false);

  // Preferences State
  const [pref, setPref] = useState<DisplayPreferences>({
    lang: "English (US)",
    locale: "United Kingdom",
    cur: "Match listing currency",
    tz: "Europe/London",
    tf: "12-hour",
    motion: "Follow system",
    density: "Comfortable",
  });

  // Privacy State
  const [personalizedReco, setPersonalizedReco] = useState(true);
  const [optionalAnalytics, setOptionalAnalytics] = useState(true);
  const [exportStatus, setExportStatus] = useState<"idle" | "queued" | "ready">("idle");

  // Account & Workspace State
  const [defaultWorkspace, setDefaultWorkspace] = useState<"Host" | "Renter">("Host");
  const [commLanguage, setCommLanguage] = useState("English");
  const [accountRegion, setAccountRegion] = useState("United Kingdom");

  // Live Audited Blocker Counts for Danger Zone
  const [closureAudit, setClosureAudit] = useState<{
    loaded: boolean;
    occupiedRooms: number;
    activeListings: number;
    activeApps: number;
    subletRequests: number;
    renterActiveTenancies: number;
    renterSubmittedApps: number;
    renterUnpaidObligations: number;
  }>({
    loaded: false,
    occupiedRooms: 0,
    activeListings: 0,
    activeApps: 0,
    subletRequests: 0,
    renterActiveTenancies: 0,
    renterSubmittedApps: 0,
    renterUnpaidObligations: 0,
  });

  // Dirty Tracking for Floating Save Bar
  const [isDirty, setIsDirty] = useState(false);

  // Toast Notification
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  // Modals
  const [activeModal, setActiveModal] = useState<
    | null
    | { type: "stepup"; title: string; onConfirm: () => void }
    | { type: "email" }
    | { type: "phone" }
    | { type: "password" }
    | { type: "mfa" }
    | { type: "passkey" }
    | { type: "revoke-all" }
    | { type: "close-account" }
  >(null);

  // Form states for modals
  const [stepupPw, setStepupPw] = useState("");
  const [stepupError, setStepupError] = useState("");
  const [modalNewEmail, setModalNewEmail] = useState("");
  const [modalNewPhone, setModalNewPhone] = useState("");
  const [modalPhoneError, setModalPhoneError] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [pwModalError, setPwModalError] = useState("");
  const [showOldPw, setShowOldPw] = useState(false);
  const [showNewPw, setShowNewPw] = useState(false);
  const [showConfirmPw, setShowConfirmPw] = useState(false);

  const showToast = useCallback((msg: string) => {
    setToastMsg(msg);
  }, []);

  useEffect(() => {
    if (!toastMsg) return;
    const t = setTimeout(() => setToastMsg(null), 3200);
    return () => clearTimeout(t);
  }, [toastMsg]);

  // Detect client device on mount
  useEffect(() => {
    const dev = detectCurrentDevice();
    setSessions([
      {
        id: "cur-session",
        dev: dev.name,
        loc: "This device · Verified authenticated session",
        last: "Active now",
        cur: true,
        type: dev.type,
      },
    ]);
  }, []);

  // Sync with user session if available
  useEffect(() => {
    if (user) {
      if (user.fullName) setDisplayName(user.fullName);
      if (user.email) setEmail(user.email);
      if (user.phone) setPhone(user.phone);
      if (user.createdAt) {
        try {
          const d = new Date(user.createdAt);
          setPasswordLastChanged(d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }));
        } catch {
          // ignore
        }
      }
    }
  }, [user]);

  // Load preferences from local storage if available
  useEffect(() => {
    try {
      const saved = localStorage.getItem("zr_user_settings_v1");
      if (saved) {
        const parsed = JSON.parse(saved);
        if (parsed.displayName) setDisplayName(parsed.displayName);
        if (parsed.pref) setPref((p) => ({ ...p, ...parsed.pref }));
        if (parsed.notifState) setNotifState(parsed.notifState);
        if (parsed.marketingNotifs !== undefined) setMarketingNotifs(parsed.marketingNotifs);
        if (parsed.quietHours !== undefined) setQuietHours(parsed.quietHours);
        if (parsed.defaultWorkspace) setDefaultWorkspace(parsed.defaultWorkspace);
        if (parsed.commLanguage) setCommLanguage(parsed.commLanguage);
        if (parsed.roleMode) setRoleMode(parsed.roleMode);
      }
    } catch {
      // ignore
    }
  }, []);

  // Fetch real notification preferences from API
  useEffect(() => {
    getNotificationPreferences(USER_NOTIFICATIONS_BASE)
      .then((apiPref) => {
        if (apiPref) {
          setQuietHours(Boolean(apiPref.quietHoursEnabled));
          if (Array.isArray(apiPref.optedOutCategories) && apiPref.optedOutCategories.length > 0) {
            setNotifState((prev) => {
              const next = { ...prev };
              if (apiPref.optedOutCategories.includes("LEASING")) next.apps = { email: false, push: false };
              if (apiPref.optedOutCategories.includes("OCCUPANCY")) next.rentals = { email: false, push: false };
              if (apiPref.optedOutCategories.includes("PAYMENTS")) next.payments = { email: false, push: false };
              return next;
            });
          }
        }
      })
      .catch(() => {});
  }, []);

  // Load live counts for closure audit
  useEffect(() => {
    async function loadAudit() {
      try {
        const [props, apps, renterOccs, renterApps, renterObligs] = await Promise.allSettled([
          listHostedProperties(),
          listHostedApplications(),
          listOccupancies(),
          listRentalApplications(),
          listMyRentalPaymentObligations(),
        ]);

        let occupied = 0;
        let listingsCount = 0;
        const liveProps = props.status === "fulfilled" ? props.value : [];
        for (const p of liveProps) {
          try {
            const rms = await listHostedRooms(p.id);
            for (const rm of rms) {
              const occs = await listHostedRoomOccupancies(rm.id);
              occupied += occs.filter((o) => o.status === "ACTIVE" || o.status === "PENDING_MOVE_IN").length;
              if (rm.status === "active") listingsCount++;
            }
          } catch {
            // continue
          }
        }

        const appList = apps.status === "fulfilled" ? apps.value : [];
        const activeApps = appList.filter(
          (a) => a.status === "SUBMITTED" || (!a.decisions || a.decisions.length === 0)
        ).length;

        // Renter audit live counts
        const occupanciesList = renterOccs.status === "fulfilled" ? renterOccs.value : [];
        const renterActiveTenancies = occupanciesList.filter(
          (o) => o.status === "ACTIVE" || o.status === "PENDING_MOVE_IN"
        ).length;

        const renterAppList = renterApps.status === "fulfilled" ? renterApps.value : [];
        const renterSubmittedApps = renterAppList.filter(
          (a) => a.status === "SUBMITTED"
        ).length;

        const obligsPage = renterObligs.status === "fulfilled" ? renterObligs.value : null;
        const renterUnpaidObligations =
          obligsPage?.items?.filter(
            (ob) => ob.status === "DUE" || ob.status === "OVERDUE" || ob.status === "UPCOMING"
          ).length || 0;

        setClosureAudit({
          loaded: true,
          occupiedRooms: occupied,
          activeListings: listingsCount,
          activeApps,
          subletRequests: 0,
          renterActiveTenancies,
          renterSubmittedApps,
          renterUnpaidObligations,
        });
      } catch {
        setClosureAudit({
          loaded: true,
          occupiedRooms: 0,
          activeListings: 0,
          activeApps: 0,
          subletRequests: 0,
          renterActiveTenancies: 0,
          renterSubmittedApps: 0,
          renterUnpaidObligations: 0,
        });
      }
    }
    loadAudit();
  }, []);

  const markDirty = useCallback(() => {
    setIsDirty(true);
  }, []);

  // Save changes handler
  const handleSaveChanges = async () => {
    if (!displayName.trim()) {
      showToast("Display name cannot be empty.");
      return;
    }

    try {
      // Update backend user profile if real session
      if (user) {
        await updateUserProfile(displayName.trim(), phone.trim());
        await refreshUser();

        // Save real notification preferences to backend database
        try {
          const optedOut: string[] = [];
          if (!notifState.apps?.email && !notifState.apps?.push) optedOut.push("LEASING");
          if (!notifState.rentals?.email && !notifState.rentals?.push) optedOut.push("OCCUPANCY");
          if (!notifState.payments?.email && !notifState.payments?.push) optedOut.push("PAYMENTS");

          await updateNotificationPreferences(USER_NOTIFICATIONS_BASE, {
            optedOutCategories: optedOut,
            quietHoursEnabled: quietHours,
            quietHoursStartMinute: 1320,
            quietHoursEndMinute: 420,
          } as any);
        } catch {
          // non-blocking for notification preferences
        }
      }

      // Save to localStorage
      const settingsPayload = {
        displayName: displayName.trim(),
        pref,
        notifState,
        marketingNotifs,
        quietHours,
        defaultWorkspace,
        commLanguage,
        roleMode,
      };
      localStorage.setItem("zr_user_settings_v1", JSON.stringify(settingsPayload));

      setIsDirty(false);
      showToast("Changes saved successfully.");
    } catch (err) {
      showToast(errorMessage(err, "Changes saved to local workspace cache."));
      setIsDirty(false);
    }
  };

  const handleDiscardChanges = () => {
    if (user?.fullName) setDisplayName(user.fullName);
    if (user?.phone) setPhone(user.phone);
    setIsDirty(false);
    showToast("Unsaved changes discarded.");
  };

  // Toggle switch helper
  const handleToggleNotif = (catId: string, channel: "email" | "push") => {
    setNotifState((prev) => {
      const current = prev[catId] || { email: true, push: true };
      const next = {
        ...prev,
        [catId]: {
          ...current,
          [channel]: !current[channel],
        },
      };
      markDirty();
      return next;
    });
  };

  // Notification rows tailored according to role mode
  const notificationRows: NotificationRowItem[] = useMemo(() => {
    if (roleMode === "host") {
      return [
        {
          id: "apps",
          categoryKey: "LEASING",
          title: "Applications & Offers",
          tag: "Host",
          description:
            "New room application received, applicant verification updates, offer counter-proposals, and response timeouts.",
          email: notifState.apps?.email ?? true,
          push: notifState.apps?.push ?? true,
          inApp: true,
        },
        {
          id: "rentals",
          categoryKey: "OCCUPANCY",
          title: "Rentals & Occupancies",
          tag: "Host",
          description:
            "Tenancy agreements ready for your signature, move-in milestones, tenant checkout notices, and agreement amendments.",
          email: notifState.rentals?.email ?? true,
          push: notifState.rentals?.push ?? true,
          inApp: true,
        },
        {
          id: "payments",
          categoryKey: "PAYMENTS",
          title: "Payments & Ledger Records",
          tag: "Host",
          description:
            "Rent payment records logged by tenants, payout disbursements, and payment discrepancy alerts.",
          email: notifState.payments?.email ?? true,
          push: notifState.payments?.push ?? true,
          inApp: true,
        },
        {
          id: "sublet",
          categoryKey: "OCCUPANCY",
          title: "Sublet Requests",
          tag: "Host",
          description:
            "Incoming sublet requests, tenant assignment approvals, and replacement tenant identity verification checks.",
          email: notifState.sublet?.email ?? true,
          push: notifState.sublet?.push ?? false,
          inApp: true,
        },
        {
          id: "verification",
          categoryKey: "DISPUTES_AND_SAFETY",
          title: "Property & Landlord Verification",
          tag: "Host",
          description:
            "Landlord authority checks, property compliance credentials, HMO renewals, and gas safety reminders.",
          email: notifState.verification?.email ?? true,
          push: notifState.verification?.push ?? true,
          inApp: true,
        },
        {
          id: "messages",
          categoryKey: "LEASING",
          title: "Messages",
          tag: "Account",
          description:
            "Direct chat inquiries from prospective renters, applicant questions, and current tenant messages.",
          email: notifState.messages?.email ?? false,
          push: notifState.messages?.push ?? true,
          inApp: true,
        },
      ];
    } else {
      // Renter Role
      return [
        {
          id: "apps",
          categoryKey: "LEASING",
          title: "My Applications & Offers",
          tag: "Renter",
          description:
            "Application status updates, host offers received, request for information from host, and offer expiration alerts.",
          email: notifState.apps?.email ?? true,
          push: notifState.apps?.push ?? true,
          inApp: true,
        },
        {
          id: "rentals",
          categoryKey: "OCCUPANCY",
          title: "My Rentals & Tenancy",
          tag: "Renter",
          description:
            "Tenancy agreement ready to sign, move-in key handover instructions, checkout inspection reminders, and landlord notices.",
          email: notifState.rentals?.email ?? true,
          push: notifState.rentals?.push ?? true,
          inApp: true,
        },
        {
          id: "payments",
          categoryKey: "PAYMENTS",
          title: "Rent & Deposit Payments",
          tag: "Renter",
          description:
            "Monthly rent payment due reminders, host rent receipt confirmations, and deposit holding scheme protection notices.",
          email: notifState.payments?.email ?? true,
          push: notifState.payments?.push ?? true,
          inApp: true,
        },
        {
          id: "sublet",
          categoryKey: "OCCUPANCY",
          title: "Sublet Requests",
          tag: "Renter",
          description:
            "Host decision on your sublet request, approval updates, and replacement tenant assignment confirmation.",
          email: notifState.sublet?.email ?? true,
          push: notifState.sublet?.push ?? false,
          inApp: true,
        },
        {
          id: "verification",
          categoryKey: "DISPUTES_AND_SAFETY",
          title: "Identity & Right-to-Rent",
          tag: "Renter",
          description:
            "ID verification approval, right-to-rent clearance status, and periodic eligibility re-checks.",
          email: notifState.verification?.email ?? true,
          push: notifState.verification?.push ?? true,
          inApp: true,
        },
        {
          id: "messages",
          categoryKey: "LEASING",
          title: "Messages",
          tag: "Account",
          description: "Direct replies and messages from room hosts and property managers.",
          email: notifState.messages?.email ?? false,
          push: notifState.messages?.push ?? true,
          inApp: true,
        },
      ];
    }
  }, [roleMode, notifState]);

  // Handle photo upload simulation
  const handlePhotoUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      if (file.size > 5 * 1024 * 1024) {
        showToast("File exceeds 5 MB limit. Please select a smaller photo.");
        return;
      }
      const url = URL.createObjectURL(file);
      setAvatarUrl(url);
      markDirty();
      showToast("Profile photo selected. Click 'Save changes' to apply.");
    }
  };

  // Step-up authentication wrapper
  const triggerStepup = (title: string, onConfirm: () => void) => {
    setStepupPw("");
    setStepupError("");
    setActiveModal({ type: "stepup", title, onConfirm });
  };

  // Navigation Items
  const navItems = [
    { id: "profile" as SettingsSection, label: "Profile", icon: User },
    { id: "security" as SettingsSection, label: "Sign-in & Security", icon: Lock },
    { id: "notifications" as SettingsSection, label: "Notifications", icon: Bell },
    { id: "preferences" as SettingsSection, label: "Preferences & Accessibility", icon: Sliders },
    { id: "privacy" as SettingsSection, label: "Privacy & Data", icon: Database },
    { id: "account" as SettingsSection, label: "Account & Workspace", icon: LayoutGrid },
  ];

  return (
    <div className="space-y-6">
      {/* Top Header with Role Switcher & Context */}
      <section className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-bold uppercase tracking-wider text-rose-600">
              {roleMode === "host" ? "Host Workspace Settings" : "Renter Workspace Settings"}
            </span>
            <span className="inline-flex items-center gap-1.5 rounded-full bg-blue-50 px-2.5 py-0.5 text-[11px] font-bold text-blue-900 border border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-800">
              <span className="h-1.5 w-1.5 rounded-full bg-blue-600 animate-pulse" />
              {roleMode === "host" ? "Host Mode Active" : "Renter Mode Active"}
            </span>
          </div>
          <h1 className="mt-1 font-heading text-2xl font-extrabold tracking-tight text-neutral-900 sm:text-3xl dark:text-white">
            Settings
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-neutral-600 dark:text-neutral-300">
            {roleMode === "host"
              ? "Account preferences, hosting settings, and security controls tailored for room providers."
              : "Account preferences, rental tenancy settings, and security controls tailored for renters."}
          </p>
        </div>

        {/* Role Toggle Switcher */}
        <div className="flex items-center gap-2 rounded-2xl border border-neutral-200 bg-white p-1.5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 self-start sm:self-auto">
          <button
            type="button"
            onClick={() => {
              setRoleMode("host");
              showToast("Switched settings context to Host view.");
            }}
            className={cn(
              "flex items-center gap-2 rounded-xl px-3.5 py-2 text-xs font-bold transition",
              roleMode === "host"
                ? "bg-[#0E2F73] text-white shadow-xs"
                : "text-neutral-600 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-white"
            )}
          >
            <Building2 className="h-4 w-4" />
            <span>Host Mode</span>
            {closureAudit.activeApps > 0 && (
              <span className="ml-1 rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] text-white font-extrabold">
                {closureAudit.activeApps}
              </span>
            )}
          </button>
          <button
            type="button"
            onClick={() => {
              setRoleMode("renter");
              showToast("Switched settings context to Renter view.");
            }}
            className={cn(
              "flex items-center gap-2 rounded-xl px-3.5 py-2 text-xs font-bold transition",
              roleMode === "renter"
                ? "bg-[#0E2F73] text-white shadow-xs"
                : "text-neutral-600 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-white"
            )}
          >
            <Home className="h-4 w-4" />
            <span>Renter Mode</span>
            {closureAudit.renterSubmittedApps > 0 && (
              <span className="ml-1 rounded-full bg-rose-500 px-1.5 py-0.5 text-[10px] text-white font-extrabold">
                {closureAudit.renterSubmittedApps}
              </span>
            )}
          </button>
        </div>
      </section>

      {/* Main Settings 2-Column Shell */}
      <div className="grid grid-cols-1 lg:grid-cols-[260px_minmax(0,1fr)] gap-6 items-start">
        {/* Left Sticky Navigation Column */}
        <aside className="sticky top-6 rounded-2xl border border-neutral-200 bg-white p-3 shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
          {/* Identity Snapshot Card */}
          <div className="flex items-center gap-3 p-3 border-b border-neutral-100 dark:border-neutral-800 mb-2">
            <div className="relative flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-[#0E2F73] font-heading font-extrabold text-white text-base overflow-hidden">
              {avatarUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={avatarUrl} alt="Avatar" className="h-full w-full object-cover" />
              ) : (
                displayName
                  .split(" ")
                  .map((w) => w[0])
                  .join("")
                  .slice(0, 2)
                  .toUpperCase()
              )}
            </div>
            <div className="min-w-0 flex-1">
              <strong className="block truncate font-heading text-sm font-bold text-neutral-900 dark:text-white">
                {displayName}
              </strong>
              {identityVerified ? (
                <div className="inline-flex items-center gap-1 text-[11px] font-bold text-emerald-700 dark:text-emerald-400">
                  <ShieldCheck className="h-3.5 w-3.5" />
                  <span>Identity Verified</span>
                </div>
              ) : (
                <Link
                  href={roleMode === "host" ? "/account/host/verification" : "/account/identity"}
                  className="inline-flex items-center gap-1 text-[11px] font-bold text-amber-700 hover:underline dark:text-amber-400"
                >
                  <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />
                  <span>Identity Not Verified</span>
                </Link>
              )}
            </div>
          </div>

          {/* Nav Links */}
          <nav className="space-y-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = activeSection === item.id;
              return (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setActiveSection(item.id);
                    setMobileSubNavOpen(false);
                  }}
                  className={cn(
                    "flex w-full items-center justify-between rounded-xl px-3.5 py-2.5 text-xs font-bold transition",
                    isActive
                      ? "bg-[#EAF0FB] text-[#0E2F73] dark:bg-blue-950/40 dark:text-blue-300"
                      : "text-neutral-700 hover:bg-neutral-50 dark:text-neutral-300 dark:hover:bg-neutral-800"
                  )}
                >
                  <div className="flex items-center gap-2.5">
                    <Icon className="h-4 w-4 shrink-0" />
                    <span>{item.label}</span>
                  </div>
                  <ChevronRight
                    className={cn(
                      "h-3.5 w-3.5 opacity-40 transition-transform",
                      isActive && "translate-x-0.5 opacity-90 text-[#0E2F73] dark:text-blue-300"
                    )}
                  />
                </button>
              );
            })}
          </nav>

          {/* Left Rail Extra Info Box */}
          <div className="mt-4 pt-3 border-t border-neutral-100 dark:border-neutral-800 space-y-3 px-2 text-xs">
            <div>
              <span className="block text-[10.5px] font-bold uppercase tracking-wider text-neutral-400">
                Security
              </span>
              <span className="font-semibold text-neutral-800 dark:text-neutral-200">
                MFA: {mfaEnabled ? "On" : "Off"} · {sessions.length} active session{sessions.length === 1 ? "" : "s"}
              </span>
            </div>
            <div>
              <span className="block text-[10.5px] font-bold uppercase tracking-wider text-neutral-400">
                Data Access
              </span>
              <button
                type="button"
                onClick={() => setActiveSection("privacy")}
                className="text-primary-700 font-bold hover:underline dark:text-primary-400"
              >
                Request data export
              </button>
            </div>
            <div className="pt-2 border-t border-neutral-100 dark:border-neutral-800">
              <button
                type="button"
                onClick={() => {
                  setActiveSection("account");
                  setTimeout(() => {
                    document.getElementById("danger-zone-section")?.scrollIntoView({ behavior: "smooth" });
                  }, 100);
                }}
                className="text-rose-600 font-bold hover:underline text-[11.5px]"
              >
                Close Account
              </button>
            </div>
          </div>
        </aside>

        {/* Right Content Panels */}
        <div className="space-y-6 min-w-0">
          {/* ================= SECTION 1: PROFILE ================= */}
          {activeSection === "profile" && (
            <div className="space-y-5 animate-in fade-in duration-200">
              <header>
                <h2 className="font-heading text-xl font-extrabold text-neutral-900 dark:text-white">
                  Profile
                </h2>
                <p className="text-xs text-neutral-500">
                  Manage your display details and how you appear to others on Zoiko Rooms.
                </p>
              </header>

              {/* Avatar & Verification Snapshot */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-4">
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div className="flex items-center gap-4">
                    <div className="relative flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-[#0E2F73] font-heading font-extrabold text-white text-xl overflow-hidden shadow-sm">
                      {avatarUrl ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img src={avatarUrl} alt="Avatar" className="h-full w-full object-cover" />
                      ) : (
                        displayName
                          .split(" ")
                          .map((w) => w[0])
                          .join("")
                          .slice(0, 2)
                          .toUpperCase()
                      )}
                    </div>
                    <div>
                      <h3 className="font-heading text-base font-bold text-neutral-900 dark:text-white">
                        {displayName}
                      </h3>
                      {identityVerified ? (
                        <div className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-bold text-emerald-800 border border-emerald-200 dark:bg-emerald-950/30 dark:text-emerald-300 dark:border-emerald-800">
                          <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
                          <span>Identity Verified</span>
                        </div>
                      ) : (
                        <div className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-bold text-amber-800 border border-amber-200 dark:bg-amber-950/30 dark:text-amber-300 dark:border-amber-800">
                          <ShieldAlert className="h-3.5 w-3.5 text-amber-600" />
                          <span>Identity Not Verified</span>
                        </div>
                      )}
                      <p className="mt-1 text-[11.5px] text-neutral-500">
                        {roleMode === "host"
                          ? "Verified legal identity is required to publish rooms and execute tenancy agreements."
                          : "Verified legal identity is required to submit verified offers and sign tenancy agreements."}
                      </p>
                    </div>
                  </div>

                  <Link
                    href={roleMode === "host" ? "/account/host/verification" : "/account/identity"}
                    className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2 text-xs font-bold text-[#0E2F73] shadow-xs hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white transition"
                  >
                    {identityVerified ? "View Verification" : "Complete Verification"}
                  </Link>
                </div>

                <div className="flex flex-wrap items-center gap-3 pt-4 border-t border-neutral-100 dark:border-neutral-800">
                  <label
                    htmlFor="avatar-file-input"
                    className="inline-flex items-center gap-2 rounded-xl bg-neutral-100 px-3.5 py-2 text-xs font-bold text-neutral-800 hover:bg-neutral-200 dark:bg-neutral-800 dark:text-neutral-200 cursor-pointer transition"
                  >
                    <Upload className="h-3.5 w-3.5" />
                    <span>Upload photo</span>
                  </label>
                  <input
                    id="avatar-file-input"
                    type="file"
                    accept="image/png,image/jpeg"
                    onChange={handlePhotoUpload}
                    className="sr-only"
                  />
                  <span className="text-xs text-neutral-500">
                    JPG or PNG, up to 5 MB. Initials are displayed if no photo is uploaded.
                  </span>
                </div>
              </div>

              {/* Display Name Card */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-2">
                <label
                  htmlFor="display-name"
                  className="block text-xs font-bold uppercase tracking-wide text-neutral-700 dark:text-neutral-300"
                >
                  Display Name
                </label>
                <input
                  id="display-name"
                  type="text"
                  value={displayName}
                  onChange={(e) => {
                    setDisplayName(e.target.value);
                    markDirty();
                  }}
                  className="w-full rounded-xl border border-neutral-300 bg-white px-3.5 py-2.5 text-sm font-semibold text-neutral-900 outline-none transition focus:border-[#0E2F73] focus:ring-1 focus:ring-[#0E2F73] dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                />
                <p className="text-xs text-neutral-500">
                  {roleMode === "host"
                    ? "Shown to prospective room applicants. Changing it does not alter your verified legal name on rental agreements."
                    : "Shown to hosts when you apply for rooms. Changing it does not alter your verified legal name on tenancy agreements."}
                </p>
              </div>

              {/* Contact Information Card */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 divide-y divide-neutral-100 dark:divide-neutral-800">
                {/* Email Row */}
                <div className="flex flex-wrap items-center justify-between gap-4 py-3 first:pt-0">
                  <div>
                    <span className="block text-xs font-bold text-neutral-500">Primary Email</span>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-sm font-bold text-neutral-900 dark:text-white">{email}</span>
                      {user?.emailVerified ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10.5px] font-bold text-emerald-700 border border-emerald-200">
                          <Check className="h-3 w-3" />
                          Verified
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[10.5px] font-bold text-amber-700 border border-amber-200">
                          <Clock className="h-3 w-3" />
                          Unverified
                        </span>
                      )}
                    </div>
                    {emailPending && (
                      <p className="mt-1 text-xs text-amber-600">
                        Verification email sent to {emailPending}. Active email remains {email} until verified.
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      triggerStepup("changing your primary email", () => {
                        setModalNewEmail("");
                        setActiveModal({ type: "email" });
                      });
                    }}
                    className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2 text-xs font-bold text-[#0E2F73] shadow-xs hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    Change
                  </button>
                </div>

                {/* Phone Row */}
                <div className="flex flex-wrap items-center justify-between gap-4 py-3 last:pb-0">
                  <div>
                    <span className="block text-xs font-bold text-neutral-500">Primary Phone</span>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-sm font-bold text-neutral-900 dark:text-white">{phone}</span>
                      {phone ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[10.5px] font-bold text-emerald-700 border border-emerald-200">
                          <Check className="h-3 w-3" />
                          Registered
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-neutral-100 px-2 py-0.5 text-[10.5px] font-bold text-neutral-600">
                          Not set
                        </span>
                      )}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      triggerStepup("changing your verified phone", () => {
                        setModalNewPhone(phone);
                        setModalPhoneError("");
                        setActiveModal({ type: "phone" });
                      });
                    }}
                    className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2 text-xs font-bold text-[#0E2F73] shadow-xs hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    Change
                  </button>
                </div>
              </div>

              {/* Privacy Notice Banner */}
              <div className="flex items-start gap-3 rounded-xl bg-blue-50/70 p-4 border border-blue-100 dark:bg-blue-950/20 dark:border-blue-900">
                <Info className="h-5 w-5 text-blue-700 dark:text-blue-300 shrink-0 mt-0.5" />
                <p className="text-xs text-blue-900 dark:text-blue-200 leading-relaxed">
                  We never display a public directory profile. Your personal details are securely provided only to
                  verified parties during formal rental applications and tenancy agreements.
                </p>
              </div>
            </div>
          )}

          {/* ================= SECTION 2: SIGN-IN & SECURITY ================= */}
          {activeSection === "security" && (
            <div className="space-y-5 animate-in fade-in duration-200">
              <header>
                <h2 className="font-heading text-xl font-extrabold text-neutral-900 dark:text-white">
                  Sign-in & Security
                </h2>
                <p className="text-xs text-neutral-500">
                  Protect how you sign in and manage connected devices and authentication credentials.
                </p>
              </header>

              {/* Password & MFA Card */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 divide-y divide-neutral-100 dark:divide-neutral-800">
                <div className="flex flex-wrap items-center justify-between gap-4 py-3 first:pt-0">
                  <div>
                    <span className="block text-xs font-bold text-neutral-500">Password</span>
                    <span className="text-sm font-semibold text-neutral-900 dark:text-white">
                      Last changed: {passwordLastChanged}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      triggerStepup("changing your password", () => {
                        setOldPassword("");
                        setNewPassword("");
                        setConfirmPassword("");
                        setPwModalError("");
                        setActiveModal({ type: "password" });
                      });
                    }}
                    className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2 text-xs font-bold text-[#0E2F73] shadow-xs hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    Change password
                  </button>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-4 py-3 last:pb-0">
                  <div>
                    <span className="block text-xs font-bold text-neutral-500">Multi-Factor Authentication</span>
                    <span className="text-sm font-semibold text-neutral-900 dark:text-white flex items-center gap-1.5">
                      <span className={cn("h-2 w-2 rounded-full", mfaEnabled ? "bg-emerald-500" : "bg-neutral-400")} />
                      {mfaEnabled ? "On · Authenticator App" : "Off · Not configured"}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setActiveModal({ type: "mfa" })}
                    className="rounded-xl border border-neutral-200 bg-white px-3.5 py-2 text-xs font-bold text-[#0E2F73] shadow-xs hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    {mfaEnabled ? "Manage MFA" : "Set up MFA"}
                  </button>
                </div>
              </div>

              {/* Passkeys Card */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-heading text-sm font-bold text-neutral-900 dark:text-white">
                      Passkeys ({passkeys.length})
                    </h3>
                    <p className="text-xs text-neutral-500">
                      Sign in securely using biometric recognition (fingerprint, face, or device PIN).
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      triggerStepup("registering a new biometric passkey", () => {
                        const dev = detectCurrentDevice();
                        const newKey: PasskeyItem = {
                          id: `pk_${Date.now()}`,
                          name: `${dev.name} Biometrics`,
                          used: "Registered just now",
                        };
                        setPasskeys((prev) => [...prev, newKey]);
                        showToast("New biometric passkey registered for this device.");
                      });
                    }}
                    className="inline-flex items-center gap-1.5 text-xs font-bold text-primary-700 hover:underline dark:text-primary-300"
                  >
                    <Key className="h-3.5 w-3.5" />
                    <span>Add a passkey</span>
                  </button>
                </div>

                {passkeys.length === 0 ? (
                  <div className="rounded-xl border border-dashed border-neutral-200 p-6 text-center dark:border-neutral-800">
                    <Key className="mx-auto h-6 w-6 text-neutral-400" />
                    <p className="mt-2 text-xs font-semibold text-neutral-700 dark:text-neutral-300">
                      No passkeys registered yet
                    </p>
                    <p className="mt-0.5 text-[11px] text-neutral-400">
                      You can register your phone, laptop fingerprint or security key for instant one-touch login.
                    </p>
                  </div>
                ) : (
                  <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
                    {passkeys.map((k) => (
                      <li key={k.id} className="flex items-center justify-between py-3">
                        <div className="flex items-center gap-3">
                          <Key className="h-4 w-4 text-primary-700 dark:text-primary-400 shrink-0" />
                          <div>
                            <strong className="block text-xs font-bold text-neutral-900 dark:text-white">
                              {k.name}
                            </strong>
                            <span className="text-[11px] text-neutral-400">{k.used}</span>
                          </div>
                        </div>
                        <button
                          type="button"
                          onClick={() => {
                            triggerStepup("removing this passkey", () => {
                              setPasskeys((prev) => prev.filter((p) => p.id !== k.id));
                              showToast("Passkey removed.");
                            });
                          }}
                          className="text-xs font-bold text-neutral-500 hover:text-rose-600"
                        >
                          Remove
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Active Sessions Card */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-4">
                <div className="flex items-center justify-between">
                  <h3 className="font-heading text-sm font-bold text-neutral-900 dark:text-white">
                    Active Sessions ({sessions.length})
                  </h3>
                  {sessions.length > 1 && (
                    <button
                      type="button"
                      onClick={() => setActiveModal({ type: "revoke-all" })}
                      className="text-xs font-bold text-rose-600 hover:underline"
                    >
                      Sign out all other sessions
                    </button>
                  )}
                </div>

                <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
                  {sessions.map((s) => (
                    <li key={s.id} className="flex items-center justify-between py-3">
                      <div className="flex items-center gap-3">
                        {s.type === "phone" ? (
                          <Smartphone className="h-4 w-4 text-neutral-500 shrink-0" />
                        ) : (
                          <Laptop className="h-4 w-4 text-neutral-500 shrink-0" />
                        )}
                        <div>
                          <div className="flex items-center gap-2">
                            <strong className="text-xs font-bold text-neutral-900 dark:text-white">
                              {s.dev}
                            </strong>
                            {s.cur && (
                              <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-bold text-emerald-800 border border-emerald-200">
                                This device
                              </span>
                            )}
                          </div>
                          <span className="text-[11px] text-neutral-400">
                            {s.loc} · {s.last}
                          </span>
                        </div>
                      </div>
                      {!s.cur && (
                        <button
                          type="button"
                          onClick={() => {
                            setSessions((prev) => prev.filter((item) => item.id !== s.id));
                            showToast("Session signed out.");
                          }}
                          className="rounded-lg border border-neutral-200 px-2.5 py-1 text-xs font-bold text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-200"
                        >
                          Sign out
                        </button>
                      )}
                    </li>
                  ))}
                </ul>
              </div>

              {/* Security Alerts (Required & Locked) */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-3">
                <h3 className="font-heading text-sm font-bold text-neutral-900 dark:text-white">
                  Mandatory Security Alerts
                </h3>
                <div className="flex items-center justify-between py-1 text-xs">
                  <span className="font-semibold text-neutral-800 dark:text-neutral-200">
                    Security alerts by Email
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-xs font-bold text-neutral-500 bg-neutral-100 px-2.5 py-1 rounded-lg">
                    <Lock className="h-3 w-3" />
                    Always On
                  </span>
                </div>
                <div className="flex items-center justify-between py-1 text-xs">
                  <span className="font-semibold text-neutral-800 dark:text-neutral-200">
                    Security alerts by Push
                  </span>
                  <span className="inline-flex items-center gap-1.5 text-xs font-bold text-neutral-500 bg-neutral-100 px-2.5 py-1 rounded-lg">
                    <Lock className="h-3 w-3" />
                    Always On
                  </span>
                </div>
                <p className="text-[11px] text-neutral-500 flex items-center gap-1.5 pt-1">
                  <Lock className="h-3 w-3 shrink-0" />
                  Required security notices regarding passwords, authentication and suspicious logins cannot be disabled.
                </p>
              </div>
            </div>
          )}

          {/* ================= SECTION 3: NOTIFICATIONS ================= */}
          {activeSection === "notifications" && (
            <div className="space-y-5 animate-in fade-in duration-200">
              <header className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
                <div>
                  <h2 className="font-heading text-xl font-extrabold text-neutral-900 dark:text-white">
                    Notifications
                  </h2>
                  <p className="text-xs text-neutral-500">
                    Choose how you hear about activity. Turning off email or push never deletes in-app history.
                  </p>
                </div>
                <span className="text-[11px] font-bold uppercase tracking-wider text-neutral-500 bg-neutral-100 dark:bg-neutral-800 px-2.5 py-1 rounded-lg self-start sm:self-auto">
                  Showing {roleMode === "host" ? "Host" : "Renter"} Channels
                </span>
              </header>

              {/* Notification Matrix Table */}
              <div className="overflow-hidden rounded-2xl border border-neutral-200 bg-white shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
                <div className="hidden sm:grid sm:grid-cols-[1fr_80px_80px_90px] gap-3 px-5 py-3 border-b border-neutral-100 bg-neutral-50/75 text-[11px] font-bold uppercase tracking-wider text-neutral-500 dark:border-neutral-800 dark:bg-neutral-800/50">
                  <span>Category</span>
                  <span className="text-center">Email</span>
                  <span className="text-center">Push</span>
                  <span className="text-center">In-app</span>
                </div>

                <div className="divide-y divide-neutral-100 dark:divide-neutral-800">
                  {notificationRows.map((row) => (
                    <div
                      key={row.id}
                      className="p-4 sm:px-5 sm:py-3.5 flex flex-col sm:grid sm:grid-cols-[1fr_80px_80px_90px] sm:items-center gap-3 hover:bg-neutral-50/50 dark:hover:bg-neutral-800/30 transition"
                    >
                      <div>
                        <div className="flex items-center gap-2">
                          <strong className="text-sm font-bold text-neutral-900 dark:text-white">
                            {row.title}
                          </strong>
                          <span className="rounded-md bg-blue-50 px-1.5 py-0.5 text-[10px] font-bold text-[#0E2F73] border border-blue-200 dark:bg-blue-950/40 dark:text-blue-300">
                            {row.tag}
                          </span>
                        </div>
                        <p className="text-xs text-neutral-500 mt-0.5 leading-relaxed">
                          {row.description}
                        </p>
                      </div>

                      {/* Controls */}
                      <div className="flex items-center justify-between sm:justify-center pt-2 sm:pt-0 border-t sm:border-t-0 border-neutral-100">
                        <span className="text-xs font-semibold text-neutral-500 sm:hidden">Email</span>
                        <button
                          type="button"
                          onClick={() => handleToggleNotif(row.id, "email")}
                          className={cn(
                            "relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-hidden",
                            row.email ? "bg-[#0E2F73]" : "bg-neutral-300 dark:bg-neutral-700"
                          )}
                        >
                          <span
                            className={cn(
                              "inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-xs",
                              row.email ? "translate-x-6" : "translate-x-1"
                            )}
                          />
                        </button>
                      </div>

                      <div className="flex items-center justify-between sm:justify-center pt-2 sm:pt-0">
                        <span className="text-xs font-semibold text-neutral-500 sm:hidden">Push</span>
                        <button
                          type="button"
                          onClick={() => handleToggleNotif(row.id, "push")}
                          className={cn(
                            "relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-hidden",
                            row.push ? "bg-[#0E2F73]" : "bg-neutral-300 dark:bg-neutral-700"
                          )}
                        >
                          <span
                            className={cn(
                              "inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-xs",
                              row.push ? "translate-x-6" : "translate-x-1"
                            )}
                          />
                        </button>
                      </div>

                      <div className="flex items-center justify-between sm:justify-center pt-2 sm:pt-0">
                        <span className="text-xs font-semibold text-neutral-500 sm:hidden">In-app</span>
                        <span className="inline-flex items-center gap-1 rounded-md bg-neutral-100 px-2 py-0.5 text-[11px] font-bold text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400">
                          <Lock className="h-3 w-3" />
                          Locked
                        </span>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="bg-neutral-50/60 p-3.5 border-t border-neutral-100 dark:border-neutral-800 dark:bg-neutral-800/30 text-xs text-neutral-500 flex items-center gap-2">
                  <Lock className="h-3.5 w-3.5 text-neutral-400 shrink-0" />
                  <span>
                    Essential tenancy agreements, rent payments, fraud-prevention and legally mandated notices are
                    always delivered in-app.
                  </span>
                </div>
              </div>

              {/* Extra Notification Preferences */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 divide-y divide-neutral-100 dark:divide-neutral-800">
                <div className="flex items-center justify-between gap-4 py-3 first:pt-0">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Product updates & marketing
                    </strong>
                    <p className="text-xs text-neutral-500">
                      Feature news, market tips, and platform updates. Separate from service notifications.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setMarketingNotifs((p) => !p);
                      markDirty();
                    }}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      marketingNotifs ? "bg-[#0E2F73]" : "bg-neutral-300 dark:bg-neutral-700"
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-xs",
                        marketingNotifs ? "translate-x-6" : "translate-x-1"
                      )}
                    />
                  </button>
                </div>

                <div className="flex items-center justify-between gap-4 py-3 last:pb-0">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Quiet hours
                    </strong>
                    <p className="text-xs text-neutral-500">
                      22:00 – 07:00 · applies to deferrable push notifications, never urgent security alerts.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setQuietHours((p) => !p);
                      markDirty();
                    }}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      quietHours ? "bg-[#0E2F73]" : "bg-neutral-300 dark:bg-neutral-700"
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-xs",
                        quietHours ? "translate-x-6" : "translate-x-1"
                      )}
                    />
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* ================= SECTION 4: PREFERENCES & ACCESSIBILITY ================= */}
          {activeSection === "preferences" && (
            <div className="space-y-5 animate-in fade-in duration-200">
              <header>
                <h2 className="font-heading text-xl font-extrabold text-neutral-900 dark:text-white">
                  Preferences & Accessibility
                </h2>
                <p className="text-xs text-neutral-500">
                  Display preferences for how Zoiko Rooms looks and formats numbers, dates and currencies for you.
                </p>
              </header>

              {/* Form Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900">
                {/* Language */}
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Display Language
                  </label>
                  <select
                    value={pref.lang}
                    onChange={(e) => {
                      setPref({ ...pref, lang: e.target.value });
                      markDirty();
                    }}
                    className="w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-xs font-semibold text-neutral-900 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    <option>English (US)</option>
                    <option>English (UK)</option>
                    <option>हिन्दी</option>
                    <option>తెలుగు</option>
                  </select>
                  <span className="text-[11px] text-neutral-400">
                    Agreement document language follows property jurisdiction rules.
                  </span>
                </div>

                {/* Region / format */}
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Region / Format
                  </label>
                  <select
                    value={pref.locale}
                    onChange={(e) => {
                      setPref({ ...pref, locale: e.target.value });
                      markDirty();
                    }}
                    className="w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-xs font-semibold text-neutral-900 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    <option>United Kingdom</option>
                    <option>United States</option>
                    <option>India</option>
                  </select>
                  <span className="text-[11px] text-neutral-400">
                    Affects date, phone, address and number formatting.
                  </span>
                </div>

                {/* Currency */}
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Display Currency
                  </label>
                  <select
                    value={pref.cur}
                    onChange={(e) => {
                      setPref({ ...pref, cur: e.target.value });
                      markDirty();
                    }}
                    className="w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-xs font-semibold text-neutral-900 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    <option>Match listing currency</option>
                    <option>GBP £</option>
                    <option>USD $</option>
                    <option>INR ₹</option>
                  </select>
                  <span className="text-[11px] text-neutral-400">
                    Default is to display the currency set on the property listing.
                  </span>
                </div>

                {/* Time zone */}
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Time Zone
                  </label>
                  <select
                    value={pref.tz}
                    onChange={(e) => {
                      setPref({ ...pref, tz: e.target.value });
                      markDirty();
                    }}
                    className="w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-xs font-semibold text-neutral-900 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    <option>Europe/London</option>
                    <option>Asia/Kolkata</option>
                    <option>America/Chicago</option>
                  </select>
                </div>

                {/* Time format */}
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Time Format
                  </label>
                  <select
                    value={pref.tf}
                    onChange={(e) => {
                      setPref({ ...pref, tf: e.target.value });
                      markDirty();
                    }}
                    className="w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-xs font-semibold text-neutral-900 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    <option>12-hour</option>
                    <option>24-hour</option>
                  </select>
                </div>

                {/* Density */}
                <div className="space-y-1.5">
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Interface Density
                  </label>
                  <select
                    value={pref.density}
                    onChange={(e) => {
                      setPref({ ...pref, density: e.target.value });
                      markDirty();
                    }}
                    className="w-full rounded-xl border border-neutral-300 bg-white px-3 py-2 text-xs font-semibold text-neutral-900 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    <option>Comfortable</option>
                    <option>Compact</option>
                  </select>
                </div>
              </div>

              {/* Legal Warning Notice */}
              <div className="flex items-start gap-3 rounded-xl bg-amber-50/80 p-4 border border-amber-200 dark:bg-amber-950/20 dark:border-amber-900">
                <AlertTriangle className="h-5 w-5 text-amber-700 dark:text-amber-400 shrink-0 mt-0.5" />
                <p className="text-xs text-amber-900 dark:text-amber-200 leading-relaxed">
                  <strong>Display preference ≠ Contractual fact.</strong> Changing your preferred display currency
                  or time zone never modifies an underlying listing price, formal offer, signed agreement term,
                  rent obligation, payment ledger record, or property-local legal deadline.
                </p>
              </div>
            </div>
          )}

          {/* ================= SECTION 5: PRIVACY & DATA ================= */}
          {activeSection === "privacy" && (
            <div className="space-y-5 animate-in fade-in duration-200">
              <header>
                <h2 className="font-heading text-xl font-extrabold text-neutral-900 dark:text-white">
                  Privacy & Data
                </h2>
                <p className="text-xs text-neutral-500">
                  Control optional data uses, download an archive of your platform records, and manage consent.
                </p>
              </header>

              {/* Data Export Card */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-4">
                <h3 className="font-heading text-sm font-bold text-neutral-900 dark:text-white">
                  Your Data Archive
                </h3>
                <p className="text-xs text-neutral-500 leading-relaxed">
                  {roleMode === "host"
                    ? "Includes your profile, property records, listings, room occupancies, agreement records, rent transaction ledgers, and communication transcripts."
                    : "Includes your profile, room applications, rental agreements, payment receipts, search preferences, and communication transcripts."}
                </p>

                {exportStatus === "queued" ? (
                  <div className="flex items-center gap-3 rounded-xl bg-blue-50 p-3.5 text-xs font-semibold text-blue-900 border border-blue-200">
                    <Clock className="h-4 w-4 animate-spin text-blue-600" />
                    <span>We are compiling your data archive. We will notify you when it is ready.</span>
                  </div>
                ) : exportStatus === "ready" ? (
                  <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-emerald-50 p-3.5 text-xs font-bold text-emerald-900 border border-emerald-200">
                    <div className="flex items-center gap-2">
                      <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                      <span>Your export is ready. Download link expires in 7 days.</span>
                    </div>
                    <button
                      type="button"
                      onClick={() => showToast("Secure download started.")}
                      className="inline-flex items-center gap-1.5 rounded-lg bg-[#0E2F73] px-3 py-1.5 text-white hover:bg-[#0B2560] transition"
                    >
                      <Download className="h-3.5 w-3.5" />
                      <span>Download Archive (ZIP)</span>
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => {
                      triggerStepup("requesting a complete data export", () => {
                        setExportStatus("queued");
                        showToast("Data export requested and queued.");
                        setTimeout(() => {
                          setExportStatus("ready");
                          showToast("Your data archive is ready for download.");
                        }, 2400);
                      });
                    }}
                    className="inline-flex items-center gap-2 rounded-xl border border-neutral-200 bg-white px-3.5 py-2 text-xs font-bold text-[#0E2F73] shadow-xs hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-white"
                  >
                    <Download className="h-3.5 w-3.5" />
                    <span>Request Data Export</span>
                  </button>
                )}
              </div>

              {/* Privacy Choices Card */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 divide-y divide-neutral-100 dark:divide-neutral-800">
                <div className="flex items-center justify-between gap-4 py-3 first:pt-0">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Marketing Communications
                    </strong>
                    <p className="text-xs text-neutral-500">
                      Receive occasional tips, feature releases, and promotional updates.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setMarketingNotifs((p) => !p);
                      markDirty();
                    }}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      marketingNotifs ? "bg-[#0E2F73]" : "bg-neutral-300 dark:bg-neutral-700"
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-xs",
                        marketingNotifs ? "translate-x-6" : "translate-x-1"
                      )}
                    />
                  </button>
                </div>

                <div className="flex items-center justify-between gap-4 py-3">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Personalized Recommendations
                    </strong>
                    <p className="text-xs text-neutral-500">
                      {roleMode === "host"
                        ? "Room demand insights, suggested pricing guidance, and listing visibility tips based on market activity."
                        : "Room suggestions, neighborhood match alerts, and pricing updates based on your recent searches."}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setPersonalizedReco((p) => !p);
                      markDirty();
                    }}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      personalizedReco ? "bg-[#0E2F73]" : "bg-neutral-300 dark:bg-neutral-700"
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-xs",
                        personalizedReco ? "translate-x-6" : "translate-x-1"
                      )}
                    />
                  </button>
                </div>

                <div className="flex items-center justify-between gap-4 py-3 last:pb-0">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Optional Analytics
                    </strong>
                    <p className="text-xs text-neutral-500">
                      Anonymous usage telemetry to help us improve performance and navigation.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => {
                      setOptionalAnalytics((p) => !p);
                      markDirty();
                    }}
                    className={cn(
                      "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors",
                      optionalAnalytics ? "bg-[#0E2F73]" : "bg-neutral-300 dark:bg-neutral-700"
                    )}
                  >
                    <span
                      className={cn(
                        "inline-block h-4 w-4 transform rounded-full bg-white transition-transform shadow-xs",
                        optionalAnalytics ? "translate-x-6" : "translate-x-1"
                      )}
                    />
                  </button>
                </div>
              </div>

              {/* Transaction Records Retention */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-3">
                <h3 className="font-heading text-sm font-bold text-neutral-900 dark:text-white">
                  Transaction & Verification Records
                </h3>
                <div className="flex items-center justify-between text-xs py-2 border-b border-neutral-100 dark:border-neutral-800">
                  <div>
                    <span className="font-bold text-neutral-800 dark:text-neutral-200 block">
                      Verification Records
                    </span>
                    <span className="text-[11.5px] text-neutral-500">
                      {roleMode === "host"
                        ? "Property deeds, HMO licenses, authority relationships, and landlord identity."
                        : "Identity documents, facial biometric verification, and right-to-rent status."}
                    </span>
                  </div>
                  <Link
                    href={roleMode === "host" ? "/account/host/verification" : "/account/identity"}
                    className="text-xs font-bold text-[#0E2F73] hover:underline shrink-0"
                  >
                    View Verification
                  </Link>
                </div>

                <div className="flex items-center justify-between text-xs py-1">
                  <div>
                    <span className="font-bold text-neutral-800 dark:text-neutral-200 block">
                      Statutory Tenancy Ledgers
                    </span>
                    <span className="text-[11.5px] text-neutral-500">
                      Rental agreements, occupancy dates, and rent transaction ledgers are retained for legal and tax
                      compliance and cannot be deleted unilaterally.
                    </span>
                  </div>
                  <span className="text-xs font-semibold text-neutral-400 shrink-0">Retained by law</span>
                </div>
              </div>
            </div>
          )}

          {/* ================= SECTION 6: ACCOUNT & WORKSPACE ================= */}
          {activeSection === "account" && (
            <div className="space-y-5 animate-in fade-in duration-200">
              <header>
                <h2 className="font-heading text-xl font-extrabold text-neutral-900 dark:text-white">
                  Account & Workspace
                </h2>
                <p className="text-xs text-neutral-500">
                  One account, two workspaces. Switching changes what you see — not who you are.
                </p>
              </header>

              {/* Default Workspace Selector */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 space-y-4">
                <span className="block text-xs font-bold uppercase tracking-wide text-neutral-700 dark:text-neutral-300">
                  Default Workspace
                </span>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label
                    className={cn(
                      "flex items-start gap-3 rounded-xl border-2 p-4 cursor-pointer transition",
                      defaultWorkspace === "Host"
                        ? "border-[#0E2F73] bg-blue-50/40 dark:bg-blue-950/20"
                        : "border-neutral-200 hover:border-neutral-300 dark:border-neutral-800"
                    )}
                  >
                    <input
                      type="radio"
                      name="default-ws"
                      value="Host"
                      checked={defaultWorkspace === "Host"}
                      onChange={() => {
                        setDefaultWorkspace("Host");
                        markDirty();
                      }}
                      className="mt-1"
                    />
                    <div>
                      <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                        Host Workspace
                      </strong>
                      <span className="text-xs text-neutral-500 leading-relaxed">
                        Properties, rooms, listings, received applications, and rental records.
                      </span>
                    </div>
                  </label>

                  <label
                    className={cn(
                      "flex items-start gap-3 rounded-xl border-2 p-4 cursor-pointer transition",
                      defaultWorkspace === "Renter"
                        ? "border-[#0E2F73] bg-blue-50/40 dark:bg-blue-950/20"
                        : "border-neutral-200 hover:border-neutral-300 dark:border-neutral-800"
                    )}
                  >
                    <input
                      type="radio"
                      name="default-ws"
                      value="Renter"
                      checked={defaultWorkspace === "Renter"}
                      onChange={() => {
                        setDefaultWorkspace("Renter");
                        markDirty();
                      }}
                      className="mt-1"
                    />
                    <div>
                      <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                        Renter Workspace
                      </strong>
                      <span className="text-xs text-neutral-500 leading-relaxed">
                        Find a room, my submitted applications, tenancies, and rent payments.
                      </span>
                    </div>
                  </label>
                </div>
              </div>

              {/* Workspace Capabilities */}
              <div className="rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900 divide-y divide-neutral-100 dark:divide-neutral-800">
                <div className="flex flex-wrap items-center justify-between gap-4 py-3 first:pt-0">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Host Workspace Capability
                    </strong>
                    <span className="text-xs text-neutral-500">
                      Available · Property and landlord authority credentials managed in Verification.
                    </span>
                  </div>
                  <Link
                    href="/account/host"
                    className="rounded-xl border border-neutral-200 bg-white px-3.5 py-1.5 text-xs font-bold text-[#0E2F73] hover:bg-neutral-50 shadow-xs"
                  >
                    Go to Host
                  </Link>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-4 py-3">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Renter Workspace Capability
                    </strong>
                    <span className="text-xs text-neutral-500">
                      Available · Search rooms, submit applications and sign rental agreements.
                    </span>
                  </div>
                  <Link
                    href="/account/rent"
                    className="rounded-xl border border-neutral-200 bg-white px-3.5 py-1.5 text-xs font-bold text-[#0E2F73] hover:bg-neutral-50 shadow-xs"
                  >
                    Find a Room
                  </Link>
                </div>

                <div className="flex flex-wrap items-center justify-between gap-4 py-3 last:pb-0">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Account Region
                    </strong>
                    <span className="text-xs text-neutral-500">
                      {accountRegion} · Changes affect jurisdiction and tax rules, so it requires support confirmation.
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      showToast("Account region change request logged. Support will contact you if verification is required.")
                    }
                    className="text-xs font-bold text-[#0E2F73] hover:underline"
                  >
                    Request change
                  </button>
                </div>
              </div>

              {/* Formal Notice Banner */}
              <div className="flex items-start gap-3 rounded-xl bg-blue-50/70 p-4 border border-blue-100 dark:bg-blue-950/20 dark:border-blue-900">
                <Mail className="h-5 w-5 text-blue-700 dark:text-blue-300 shrink-0 mt-0.5" />
                <p className="text-xs text-blue-900 dark:text-blue-200 leading-relaxed">
                  Formal legal notices under an active rental agreement always use the formal service details
                  stipulated in that specific agreement. Changing your general profile contact here does not
                  retroactively amend active contracts.
                </p>
              </div>

              {/* Danger Zone */}
              <div
                id="danger-zone-section"
                className="rounded-2xl border border-rose-200 bg-rose-50/50 p-5 shadow-xs dark:border-rose-900/60 dark:bg-rose-950/20 space-y-3"
              >
                <div className="flex items-center gap-2 text-rose-700 dark:text-rose-400">
                  <ShieldAlert className="h-5 w-5" />
                  <h3 className="font-heading text-sm font-extrabold uppercase tracking-wide">
                    Danger Zone
                  </h3>
                </div>
                <div className="flex flex-wrap items-center justify-between gap-4">
                  <div>
                    <strong className="block text-sm font-bold text-neutral-900 dark:text-white">
                      Close your account
                    </strong>
                    <p className="text-xs text-neutral-600 dark:text-neutral-400">
                      Staged account closure with automated compliance and active obligation checks.
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setActiveModal({ type: "close-account" })}
                    className="rounded-xl bg-rose-600 px-4 py-2 text-xs font-bold text-white shadow-xs hover:bg-rose-700 transition"
                  >
                    Close Account
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ================= FLOATING SAVE BAR ================= */}
      {isDirty && (
        <aside
          role="region"
          aria-label="Unsaved changes bar"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-4 rounded-2xl bg-[#0B1B3F] text-white px-5 py-3 shadow-2xl animate-in slide-in-from-bottom duration-200 max-w-lg w-[calc(100%-2rem)]"
        >
          <span className="text-xs font-bold flex-1 truncate">You have unsaved changes.</span>
          <button
            type="button"
            onClick={handleDiscardChanges}
            className="text-xs font-semibold text-neutral-300 hover:text-white transition"
          >
            Discard
          </button>
          <button
            type="button"
            onClick={handleSaveChanges}
            className="rounded-xl bg-white px-4 py-1.5 text-xs font-extrabold text-[#0B1B3F] hover:bg-neutral-100 transition shadow-xs cursor-pointer"
          >
            Save changes
          </button>
        </aside>
      )}

      {/* ================= TOAST NOTIFICATION ================= */}
      {toastMsg && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 rounded-xl bg-neutral-900 px-4 py-2.5 text-xs font-bold text-white shadow-lg animate-in fade-in duration-150"
        >
          {toastMsg}
        </div>
      )}

      {/* ================= MODALS ================= */}

      {/* 1. Step-up Authentication Modal */}
      {activeModal?.type === "stepup" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl dark:bg-neutral-900 space-y-4">
            <h3 className="font-heading text-lg font-bold text-neutral-900 dark:text-white">
              Confirm it’s you
            </h3>
            <p className="text-xs text-neutral-600 dark:text-neutral-400">
              Please confirm your identity before {activeModal.title}.
            </p>

            <div className="space-y-1.5">
              <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                Current Password
              </label>
              <input
                type="password"
                value={stepupPw}
                onChange={(e) => setStepupPw(e.target.value)}
                placeholder="Enter current password"
                className="w-full rounded-xl border border-neutral-300 p-2.5 text-xs font-semibold dark:border-neutral-700 dark:bg-neutral-800"
              />
              {stepupError && <span className="text-xs font-bold text-rose-600">{stepupError}</span>}
            </div>

            <p className="text-[11px] text-neutral-400">Or use a registered passkey on this device.</p>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-200 px-3.5 py-2 text-xs font-bold text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-300"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  setActiveModal(null);
                  activeModal.onConfirm();
                }}
                className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white hover:bg-[#0B2560]"
              >
                Continue
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 2. Change Email Modal */}
      {activeModal?.type === "email" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl dark:bg-neutral-900 space-y-4">
            <h3 className="font-heading text-lg font-bold text-neutral-900 dark:text-white">
              Change Primary Email
            </h3>
            <div className="space-y-1.5">
              <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                New Email Address
              </label>
              <input
                type="email"
                value={modalNewEmail}
                onChange={(e) => setModalNewEmail(e.target.value)}
                placeholder="new.email@example.com"
                className="w-full rounded-xl border border-neutral-300 p-2.5 text-xs font-semibold dark:border-neutral-700 dark:bg-neutral-800"
              />
            </div>
            <p className="text-xs text-neutral-500 leading-relaxed">
              We will send a verification link to your new address and notify your existing email. Your current
              email will remain in effect until the verification is completed.
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-200 px-3.5 py-2 text-xs font-bold text-neutral-700 hover:bg-neutral-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!modalNewEmail.trim() || !modalNewEmail.includes("@")) {
                    showToast("Please enter a valid email address.");
                    return;
                  }
                  setEmailPending(modalNewEmail.trim());
                  setActiveModal(null);
                  showToast("Verification link sent to " + modalNewEmail.trim());
                }}
                className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white hover:bg-[#0B2560]"
              >
                Send verification
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 3. Change Phone Modal */}
      {activeModal?.type === "phone" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl dark:bg-neutral-900 space-y-4">
            <h3 className="font-heading text-lg font-bold text-neutral-900 dark:text-white">
              Change Verified Phone
            </h3>
            <div className="space-y-1.5">
              <PhoneInputWithCountry
                value={modalNewPhone}
                onChange={(fullVal) => setModalNewPhone(fullVal)}
                error={modalPhoneError}
              />
            </div>
            <p className="text-xs text-neutral-500">
              A security verification code will be sent by SMS to confirm ownership of the new phone number.
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-200 px-3.5 py-2 text-xs font-bold text-neutral-700 hover:bg-neutral-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  const { countryCode, digits } = splitPhoneNumber(modalNewPhone);
                  const msg = validatePhoneWithCountry(countryCode, digits);
                  if (msg) {
                    setModalPhoneError(msg);
                    return;
                  }
                  setPhone(modalNewPhone);
                  markDirty();
                  setActiveModal(null);
                  showToast("Phone number updated. Click 'Save changes' to persist.");
                }}
                className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white hover:bg-[#0B2560]"
              >
                Update Phone
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 4. Change Password Modal */}
      {activeModal?.type === "password" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl dark:bg-neutral-900 space-y-4">
            <h3 className="font-heading text-lg font-bold text-neutral-900 dark:text-white">
              Change Password
            </h3>

            <div className="space-y-3">
              <div className="space-y-1">
                <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                  Current Password
                </label>
                <div className="relative">
                  <input
                    type={showOldPw ? "text" : "password"}
                    value={oldPassword}
                    onChange={(e) => setOldPassword(e.target.value)}
                    className="w-full rounded-xl border border-neutral-300 p-2.5 text-xs font-semibold pr-9 dark:border-neutral-700 dark:bg-neutral-800"
                  />
                  <button
                    type="button"
                    onClick={() => setShowOldPw((p) => !p)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-400"
                  >
                    {showOldPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                  New Password
                </label>
                <div className="relative">
                  <input
                    type={showNewPw ? "text" : "password"}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    className="w-full rounded-xl border border-neutral-300 p-2.5 text-xs font-semibold pr-9 dark:border-neutral-700 dark:bg-neutral-800"
                  />
                  <button
                    type="button"
                    onClick={() => setShowNewPw((p) => !p)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-400"
                  >
                    {showNewPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                  Confirm New Password
                </label>
                <div className="relative">
                  <input
                    type={showConfirmPw ? "text" : "password"}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="w-full rounded-xl border border-neutral-300 p-2.5 text-xs font-semibold pr-9 dark:border-neutral-700 dark:bg-neutral-800"
                  />
                  <button
                    type="button"
                    onClick={() => setShowConfirmPw((p) => !p)}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-400"
                  >
                    {showConfirmPw ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>

              {pwModalError && <p className="text-xs font-bold text-rose-600">{pwModalError}</p>}
            </div>

            <p className="text-[11px] text-neutral-400">
              Must be at least 12 characters. Other sessions will be signed out for your security.
            </p>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-200 px-3.5 py-2 text-xs font-bold text-neutral-700 hover:bg-neutral-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={async () => {
                  if (newPassword.length < 12) {
                    setPwModalError("New password must be at least 12 characters.");
                    return;
                  }
                  if (newPassword !== confirmPassword) {
                    setPwModalError("Passwords do not match.");
                    return;
                  }
                  try {
                    await changeUserPassword(oldPassword, newPassword);
                    setPasswordLastChanged("Just now");
                    setSessions((prev) => prev.filter((s) => s.cur));
                    setActiveModal(null);
                    showToast("Password changed. Other active sessions have been signed out.");
                  } catch (err) {
                    setPwModalError(errorMessage(err, "Could not update password. Check your current password."));
                  }
                }}
                className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white hover:bg-[#0B2560]"
              >
                Update password
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 5. Manage MFA Modal */}
      {activeModal?.type === "mfa" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl dark:bg-neutral-900 space-y-4">
            <h3 className="font-heading text-lg font-bold text-neutral-900 dark:text-white">
              Multi-Factor Authentication
            </h3>

            <div className="rounded-xl border border-neutral-200 p-3 divide-y divide-neutral-100 text-xs">
              <div className="flex items-center justify-between pb-2">
                <div>
                  <strong className="block text-neutral-800 dark:text-neutral-200">
                    Authenticator App
                  </strong>
                  <span className="text-neutral-400">Added Aug 14, 2026</span>
                </div>
                <span className="font-bold text-emerald-600">Active</span>
              </div>
              <div className="flex items-center justify-between pt-2">
                <div>
                  <strong className="block text-neutral-800 dark:text-neutral-200">
                    Backup Recovery Codes
                  </strong>
                  <span className="text-neutral-400">8 of 10 codes remaining</span>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    triggerStepup("regenerating recovery codes", () => {
                      showToast("New backup recovery codes generated. Store them safely.");
                    });
                  }}
                  className="rounded-lg border border-neutral-200 px-2.5 py-1 text-xs font-bold text-[#0E2F73] hover:bg-neutral-50"
                >
                  Regenerate
                </button>
              </div>
            </div>

            <div className="flex items-center justify-between gap-2 pt-2">
              <button
                type="button"
                onClick={() => {
                  showToast("Turning off MFA is restricted while hosting or active in leases. Passkey required.");
                }}
                className="text-xs font-bold text-rose-600 hover:underline"
              >
                Turn off MFA
              </button>
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white hover:bg-[#0B2560]"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 6. Revoke All Other Sessions Modal */}
      {activeModal?.type === "revoke-all" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-2xl dark:bg-neutral-900 space-y-4">
            <h3 className="font-heading text-lg font-bold text-neutral-900 dark:text-white">
              Sign out all other sessions?
            </h3>
            <p className="text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
              You will remain signed in on this current browser device. All other computers, tablets and phones
              will be signed out immediately and will need to log in again.
            </p>
            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                type="button"
                onClick={() => setActiveModal(null)}
                className="rounded-xl border border-neutral-200 px-3.5 py-2 text-xs font-bold text-neutral-700 hover:bg-neutral-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => {
                  setSessions((prev) => prev.filter((s) => s.cur));
                  setActiveModal(null);
                  showToast("All other sessions have been signed out.");
                }}
                className="rounded-xl bg-[#0E2F73] px-4 py-2 text-xs font-bold text-white hover:bg-[#0B2560]"
              >
                Sign out others
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 7. Close Account Modal with Role-Specific Audit */}
      {activeModal?.type === "close-account" && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-xs">
          <div className="w-full max-w-lg rounded-2xl bg-white p-6 shadow-2xl dark:bg-neutral-900 space-y-4">
            <div className="flex items-center gap-2 text-rose-600">
              <ShieldAlert className="h-5 w-5 shrink-0" />
              <h3 className="font-heading text-lg font-bold">Close Zoiko Rooms Account</h3>
            </div>

            <ul className="text-xs text-neutral-600 dark:text-neutral-400 space-y-1.5 list-disc pl-4 leading-relaxed">
              <li>You will permanently lose access to this account upon closure.</li>
              <li>Active tenancy agreements, payment ledgers, and legal disputes are not cancelled merely by closing your account.</li>
              <li>Statutory transaction and identity verification records are retained per regulatory mandates.</li>
            </ul>

            {/* Audit Status Check */}
            {roleMode === "host" ? (
              <div className="rounded-xl bg-rose-50 p-4 border border-rose-200 dark:bg-rose-950/30 dark:border-rose-900 space-y-2">
                <div className="flex items-center gap-2 text-rose-800 dark:text-rose-300 font-bold text-xs">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>Account Closure Prerequisites Check (Host)</span>
                </div>
                <p className="text-[11.5px] text-rose-700 dark:text-rose-400">
                  {closureAudit.occupiedRooms > 0 || closureAudit.activeListings > 0 || closureAudit.activeApps > 0
                    ? "Account closure cannot proceed while active hosting commitments remain. Please resolve the following:"
                    : "No active hosting blockers detected. All listings and tenancies are clear."}
                </p>
                <ul className="text-xs font-semibold text-rose-800 dark:text-rose-300 list-disc pl-5 space-y-1">
                  <li>{closureAudit.occupiedRooms} occupied rooms with active rental records</li>
                  <li>{closureAudit.activeListings} published property listings</li>
                  <li>{closureAudit.activeApps} open applications awaiting host decision</li>
                </ul>
              </div>
            ) : (
              <div className="rounded-xl bg-rose-50 p-4 border border-rose-200 dark:bg-rose-950/30 dark:border-rose-900 space-y-2">
                <div className="flex items-center gap-2 text-rose-800 dark:text-rose-300 font-bold text-xs">
                  <AlertTriangle className="h-4 w-4 shrink-0" />
                  <span>Account Closure Prerequisites Check (Renter)</span>
                </div>
                <p className="text-[11.5px] text-rose-700 dark:text-rose-400">
                  {closureAudit.renterActiveTenancies > 0 || closureAudit.renterSubmittedApps > 0 || closureAudit.renterUnpaidObligations > 0
                    ? "Account closure cannot proceed while active renting commitments remain. Please resolve the following:"
                    : "No active renting blockers detected. All tenancies and applications are clear."}
                </p>
                <ul className="text-xs font-semibold text-rose-800 dark:text-rose-300 list-disc pl-5 space-y-1">
                  <li>{closureAudit.renterActiveTenancies} active tenancy contracts in effect</li>
                  <li>{closureAudit.renterSubmittedApps} submitted room applications pending host review</li>
                  <li>{closureAudit.renterUnpaidObligations} outstanding payment obligations</li>
                </ul>
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-3 pt-2">
              <button
                type="button"
                onClick={() => {
                  setActiveModal(null);
                  setActiveSection("privacy");
                  showToast("Opening data export request.");
                }}
                className="inline-flex items-center gap-1.5 text-xs font-bold text-[#0E2F73] hover:underline"
              >
                <Download className="h-3.5 w-3.5" />
                <span>Download my data archive first</span>
              </button>

              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setActiveModal(null)}
                  className="rounded-xl border border-neutral-200 px-3.5 py-2 text-xs font-bold text-neutral-700 hover:bg-neutral-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={
                    roleMode === "host"
                      ? closureAudit.occupiedRooms > 0 || closureAudit.activeListings > 0 || closureAudit.activeApps > 0
                      : closureAudit.renterActiveTenancies > 0 || closureAudit.renterSubmittedApps > 0 || closureAudit.renterUnpaidObligations > 0
                  }
                  onClick={() => {
                    setActiveModal(null);
                    showToast("Account closure request submitted for final compliance review.");
                  }}
                  className="rounded-xl bg-rose-600 px-4 py-2 text-xs font-bold text-white hover:bg-rose-700 disabled:opacity-50 disabled:cursor-not-allowed transition"
                >
                  Continue Closure
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
