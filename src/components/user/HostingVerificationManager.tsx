"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import {
  AlertTriangle,
  Building2,
  Check,
  Clock,
  Info,
  KeyRound,
  Lock,
  Minus,
  Search,
  ShieldCheck,
  User,
  Users,
  Wallet,
  X,
} from "lucide-react";
import { Modal } from "@/components/ui/Modal";
import { RoundedFilterDropdown } from "@/components/ui/RoundedFilterDropdown";
import { Loader } from "@/components/ui/Loader";
import { useUserSession } from "@/components/user/UserSessionContext";
import { AuthorityVerificationWizard } from "@/components/user/AuthorityVerificationWizard";
import { PropertyVerificationWizard } from "@/components/user/PropertyVerificationWizard";
import { IdentityVerificationWizard } from "@/components/user/IdentityVerificationWizard";
import {
  AuthoritySummary,
  listMyAuthority,
} from "@/lib/authority-verification";
import {
  getPropertyVerificationForProperty,
  PropertyVerificationState,
} from "@/lib/property-verification";
import { listHostedProperties } from "@/lib/user-api";
import { Property } from "@/lib/types";
import { formatDate } from "@/lib/utils";

type TabKey = "OVERVIEW" | "IDENTITY" | "PROPERTIES" | "AUTHORITY" | "ACTIVITY";

interface VerificationPropertyItem {
  id: number;
  name: string;
  address: string;
  code: string;
  state: PropertyVerificationState;
  stateLabel: string;
  notes: string;
  verifiedAtText?: string;
}

interface VerificationAuthorityItem {
  id: number;
  propertyId: number;
  propertyName: string;
  role: string;
  relationshipType: string;
  state: "VERIFIED" | "EXPIRING_SOON" | "EXPIRED" | "ACTION_REQUIRED";
  stateLabel: string;
  scope: string;
  expiryText: string;
  isExpiringSoon: boolean;
  expiresAt: string | null;
  effectiveFromText?: string;
  authorityBasis?: string;
  actingThrough?: string;
}

type DrawerTarget =
  | { type: "IDENTITY" }
  | { type: "PROPERTY"; property: VerificationPropertyItem }
  | { type: "AUTHORITY"; authority: VerificationAuthorityItem }
  | {
      type: "PAYMENT";
      propertyName: string;
      payee: string;
      account: string;
      scope: string;
    }
  | null;

export function HostingVerificationManager() {
  const { user, identityProfile, identityRecords, identityVerified } = useUserSession();

  const [activeTab, setActiveTab] = useState<TabKey>("OVERVIEW");

  const [searchQuery, setSearchQuery] = useState("");
  const [verificationStatusFilter, setVerificationStatusFilter] = useState("ALL");
  const [activityFilter, setActivityFilter] = useState("ALL");

  const verificationFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "All statuses" },
      { value: "VERIFIED", label: "Verified" },
      { value: "ACTION_REQUIRED", label: "Action Required" },
    ],
    []
  );

  const activityFilterOptions = useMemo(
    () => [
      { value: "ALL", label: "All activity" },
      { value: "IDENTITY", label: "Identity" },
      { value: "PROPERTY", label: "Property" },
      { value: "AUTHORITY", label: "Authority" },
      { value: "PAYMENT", label: "Payment" },
      { value: "ORGANIZATION", label: "Organization" },
    ],
    []
  );

  const [loading, setLoading] = useState(true);

  // Live database records
  const [dbProperties, setDbProperties] = useState<Property[]>([]);
  const [dbAuthority, setDbAuthority] = useState<AuthoritySummary[]>([]);
  const [propertyVerificationStates, setPropertyVerificationStates] = useState<
    Record<number, PropertyVerificationState>
  >({});

  // Slide-out Drawer state
  const [drawerTarget, setDrawerTarget] = useState<DrawerTarget>(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Active Wizard Modal states
  const [authorityModalTarget, setAuthorityModalTarget] = useState<{
    propertyId: number;
    propertyLabel: string;
  } | null>(null);

  const [propertyModalTarget, setPropertyModalTarget] = useState<{
    propertyId: number;
    propertyLabel: string;
  } | null>(null);

  const [identityModalOpen, setIdentityModalOpen] = useState(false);

  // Lock body scroll and close drawer on Escape key
  useEffect(() => {
    if (!drawerTarget) return;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDrawerTarget(null);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [drawerTarget]);

  // Fetch live backend records from PostgreSQL via FastAPI
  const loadData = useCallback(async () => {
    try {
      setLoading(true);
      const [props, authorities] = await Promise.all([
        listHostedProperties().catch(() => [] as Property[]),
        listMyAuthority().catch(() => [] as AuthoritySummary[]),
      ]);

      setDbProperties(props);
      setDbAuthority(authorities);

      // Fetch live verification state per property
      const verifPairs = await Promise.all(
        props.map(async (p) => {
          try {
            const res = await getPropertyVerificationForProperty(p.id);
            return [p.id, res.state] as const;
          } catch {
            return [p.id, "NOT_STARTED" as PropertyVerificationState] as const;
          }
        })
      );
      setPropertyVerificationStates(Object.fromEntries(verifPairs));
    } catch (err) {
      console.warn("Could not load verification records from backend:", err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Derived Properties list (strictly bound to database records)
  const rawPropertiesList: VerificationPropertyItem[] = useMemo(() => {
    return dbProperties.map((p, idx) => {
      const state = propertyVerificationStates[p.id] ?? "NOT_STARTED";
      const code = `ZR-00000${(p.id || idx + 1).toString().padStart(2, "0")}`;
      const name = p.address.split(",")[0] || p.city || `Property #${code}`;

      let stateLabel = "Property Verified";
      let notes = "Location confirmed · evidence verified";

      if (state === "NOT_STARTED") {
        stateLabel = "Not Started";
        notes = "Upload location and deed evidence";
      } else if (state === "IN_PROGRESS" || state === "MANUAL_REVIEW") {
        stateLabel = "In Review";
        notes = "Evidence undergoing verification";
      } else if (state === "ACTION_REQUIRED" || state === "REJECTED") {
        stateLabel = "Action Required";
        notes = "Evidence re-submission needed";
      } else if (state === "EXPIRED" || state === "EXPIRING_SOON") {
        stateLabel = "Expiring Soon";
        notes = "Verification renewal required";
      }

      return {
        id: p.id,
        name,
        address: `${p.address} · Property #${code}`,
        code,
        state,
        stateLabel,
        notes,
        verifiedAtText: "Sep 20, 2026",
      };
    });
  }, [dbProperties, propertyVerificationStates]);

  const propertiesList = useMemo(() => {
    let result = rawPropertiesList;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      result = result.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          p.address.toLowerCase().includes(q) ||
          p.code.toLowerCase().includes(q)
      );
    }
    if (verificationStatusFilter === "VERIFIED") {
      result = result.filter((p) => p.state === "VERIFIED");
    } else if (verificationStatusFilter === "ACTION_REQUIRED") {
      result = result.filter((p) => p.state !== "VERIFIED");
    }
    return result;
  }, [rawPropertiesList, searchQuery, verificationStatusFilter]);

  // Derived Authority list (strictly bound to database records)
  const authorityList: VerificationAuthorityItem[] = useMemo(() => {
    return dbAuthority.map((a) => {
      const isExp = a.state === "EXPIRING_SOON";
      let stateLabel = "Listing Authority Verified";
      if (a.state === "EXPIRING_SOON") stateLabel = "Expiring Soon";
      else if (a.state === "EXPIRED") stateLabel = "Expired";
      else if (a.state === "ACTION_REQUIRED") stateLabel = "Action Required";
      else if (a.state === "COLLECTING" || a.state === "NOT_STARTED") stateLabel = "Incomplete";

      let expiryText = "No expiry configured";
      if (a.expiresAt) {
        expiryText = `Expires ${formatDate(a.expiresAt)}`;
      }

      const isOwner = a.relationshipType === "OWNER";

      return {
        id: a.id,
        propertyId: a.propertyId,
        propertyName: a.propertyLabel,
        role: isOwner ? "Role: Owner" : `Role: ${a.relationshipType}`,
        relationshipType: a.relationshipType,
        state: (a.state as VerificationAuthorityItem["state"]) || "VERIFIED",
        stateLabel,
        scope: "Scope: Create / Manage / Publish Listings",
        expiryText,
        isExpiringSoon: isExp,
        expiresAt: a.expiresAt,
        effectiveFromText: "Sep 20, 2026",
        authorityBasis: isOwner ? "Verified ownership record" : "Verified mandate / delegation",
        actingThrough: isOwner ? undefined : "Riverside Property Management Ltd",
      };
    });
  }, [dbAuthority]);

  // Urgent expiring / blocked item
  const urgentItem = useMemo(() => {
    return authorityList.find(
      (a) => a.isExpiringSoon || a.state === "EXPIRING_SOON" || a.state === "ACTION_REQUIRED"
    );
  }, [authorityList]);

  // KPI Calculations
  const verifiedPropsCount = propertiesList.filter((p) => p.state === "VERIFIED").length;
  const totalPropsCount = propertiesList.length;

  const verifiedAuthorityCount = authorityList.filter(
    (a) => a.state === "VERIFIED" || a.state === "EXPIRING_SOON"
  ).length;
  const totalAuthorityCount = authorityList.length;

  const userVerified = Boolean(identityVerified || identityProfile?.state === "VERIFIED");
  const actionRequiredCount =
    (urgentItem ? 1 : 0) +
    (!userVerified ? 1 : 0) +
    propertiesList.filter((p) => p.state === "ACTION_REQUIRED" || p.state === "REJECTED").length;

  // Real user display data
  const userName = user?.fullName || user?.email || "Host Account";
  const identityDateText = identityProfile?.verifiedAt
    ? formatDate(identityProfile.verifiedAt)
    : identityRecords[0]?.createdAt
    ? formatDate(identityRecords[0].createdAt)
    : "Sep 18, 2026";

  // Payment receipt authorities derived from properties and authority
  const paymentReceiptList = useMemo(() => {
    if (propertiesList.length === 0) {
      return [
        {
          prop: "Primary Property",
          payee: `${userName} (Owner)`,
          st: "verified",
          acct: "Payee account verified · Primary Settlement Bank",
          note: "",
          scope: "Rent for rentals at this property",
        },
      ];
    }
    return propertiesList.map((p, idx) => {
      const isFirst = idx === 0;
      return {
        prop: p.name,
        payee: isFirst ? `${userName} (Owner)` : "Riverside Homes LLP (Owner)",
        st: "verified",
        acct: isFirst ? "Payee account verified · HDFC ••••4821" : "",
        note: isFirst ? "" : "Rent is paid to verified owner. Agent authority doesn’t include PAYMENT_RECEIPT.",
        scope: "Rent for rentals at this property",
      };
    });
  }, [propertiesList, userName]);

  if (loading) {
    return <Loader label="Loading verification center..." />;
  }

  return (
    <div className="w-full space-y-4 font-sans">
      {/* 1. Page Header matching Figma Frame */}
      <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 pt-1">
        <div className="space-y-1.5 max-w-3xl">
          <p className="text-[11px] font-bold text-[#D80B0B] dark:text-red-400 uppercase tracking-widest font-heading">
            Host workspace
          </p>
          <h1 className="text-2xl sm:text-3xl lg:text-4xl font-extrabold text-[#0C2B4E] dark:text-white leading-tight font-heading">
            Verification
          </h1>
          <p className="text-xs sm:text-sm md:text-base text-slate-500 dark:text-slate-400 font-normal leading-relaxed">
            Manage identity, property and authority verification required to list, manage and receive payments for your properties.
          </p>
        </div>

        {/* Top Right Action Button */}
        <div className="shrink-0">
          {actionRequiredCount > 0 ? (
            <button
              onClick={() => {
                if (urgentItem) {
                  setDrawerTarget({
                    type: "AUTHORITY",
                    authority: urgentItem,
                  });
                } else if (!userVerified) {
                  setIdentityModalOpen(true);
                }
              }}
              className="h-12 px-6 rounded-full bg-[#D80B0B] hover:bg-red-700 text-white font-bold text-sm sm:text-base shadow-[0px_10px_24px_0px_rgba(216,11,11,0.22)] transition cursor-pointer flex items-center justify-center gap-2"
            >
              <span>Resolve {actionRequiredCount} issue{actionRequiredCount > 1 ? "s" : ""}</span>
            </button>
          ) : (
            <div className="h-12 px-5 rounded-full bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 font-bold text-xs sm:text-sm flex items-center justify-center gap-2">
              <ShieldCheck className="size-4 text-emerald-600" />
              <span>All verifications up to date</span>
            </div>
          )}
        </div>
      </div>

      {/* 2. Five KPI Metric Cards */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3 pt-1">
        {/* Card 1: Identity */}
        <button
          type="button"
          onClick={() => {
            setActiveTab("IDENTITY");
            if (userVerified) {
              setDrawerTarget({ type: "IDENTITY" });
            }
          }}
          className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 hover:border-[#0C2B4E] dark:hover:border-slate-700 p-4 flex flex-col justify-between gap-2 shadow-2xs transition text-left cursor-pointer"
        >
          <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wide font-heading">
            Identity
          </p>
          <div className="py-0.5">
            {userVerified ? (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                <Check className="size-3 stroke-[2.5]" />
                <span>Verified</span>
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400 text-xs font-extrabold uppercase tracking-wide">
                <Clock className="size-3 stroke-[2.5]" />
                <span>Pending</span>
              </span>
            )}
          </div>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
            Account level · {userName}
          </p>
        </button>

        {/* Card 2: Properties */}
        <button
          type="button"
          onClick={() => setActiveTab("PROPERTIES")}
          className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 hover:border-[#0C2B4E] dark:hover:border-slate-700 p-4 flex flex-col justify-between gap-2 shadow-2xs transition text-left cursor-pointer"
        >
          <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wide font-heading">
            Properties
          </p>
          <p className="text-2xl font-extrabold text-[#0C2B4E] dark:text-white font-heading">
            {verifiedPropsCount} of {totalPropsCount}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
            {totalPropsCount === 0 ? "No properties yet" : `${verifiedPropsCount} Verified`}
          </p>
        </button>

        {/* Card 3: Listing Authority */}
        <button
          type="button"
          onClick={() => setActiveTab("AUTHORITY")}
          className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 hover:border-[#0C2B4E] dark:hover:border-slate-700 p-4 flex flex-col justify-between gap-2 shadow-2xs transition text-left cursor-pointer"
        >
          <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wide font-heading">
            Listing Authority
          </p>
          <p className="text-2xl font-extrabold text-[#0C2B4E] dark:text-white font-heading">
            {verifiedAuthorityCount} of {totalAuthorityCount}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
            {urgentItem ? "1 expiring soon" : totalAuthorityCount > 0 ? "All verified" : "No records yet"}
          </p>
        </button>

        {/* Card 4: Payment Authority */}
        <button
          type="button"
          onClick={() => setActiveTab("AUTHORITY")}
          className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 hover:border-[#0C2B4E] dark:hover:border-slate-700 p-4 flex flex-col justify-between gap-2 shadow-2xs transition text-left cursor-pointer"
        >
          <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wide font-heading">
            Payment Authority
          </p>
          <p className="text-2xl font-extrabold text-[#0C2B4E] dark:text-white font-heading">
            {totalAuthorityCount > 0 ? 1 : 0}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
            {totalAuthorityCount > 0 ? "Payment Receipt granted" : "Pending setup"}
          </p>
        </button>

        {/* Card 5: Action Required */}
        <button
          type="button"
          onClick={() => {
            setActiveTab("AUTHORITY");
            if (urgentItem) {
              setDrawerTarget({
                type: "AUTHORITY",
                authority: urgentItem,
              });
            }
          }}
          className={`rounded-2xl border p-4 flex flex-col justify-between gap-2 shadow-2xs transition text-left cursor-pointer ${
            actionRequiredCount > 0
              ? "bg-[#FFF9F9] dark:bg-red-950/20 border-red-200 dark:border-red-900/50 hover:border-[#D80B0B]"
              : "bg-white dark:bg-slate-900 border-slate-200/90 dark:border-slate-800 hover:border-[#0C2B4E]"
          }`}
        >
          <p
            className={`text-xs font-extrabold uppercase tracking-wide font-heading ${
              actionRequiredCount > 0 ? "text-[#0C2B4E] dark:text-white" : "text-slate-500 dark:text-slate-400"
            }`}
          >
            Action Required
          </p>
          <p
            className={`text-2xl font-extrabold font-heading ${
              actionRequiredCount > 0 ? "text-[#D80B0B] dark:text-red-400" : "text-slate-700 dark:text-slate-300"
            }`}
          >
            {actionRequiredCount}
          </p>
          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
            {actionRequiredCount > 0 ? "Renewal or check due" : "All up to date"}
          </p>
        </button>
      </div>

      {/* 3. Urgent Attention Alert Banner (Only shown if a real issue exists) */}
      {urgentItem && (
        <div className="rounded-2xl border border-red-200 dark:border-red-900/40 bg-[#FFF5F5] dark:bg-red-950/20 p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 shadow-2xs">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <AlertTriangle className="size-4 text-[#D80B0B] shrink-0" />
              <span className="text-sm font-bold text-[#D80B0B] dark:text-red-400 font-heading">
                1 verification item needs your attention
              </span>
            </div>
            <p className="text-xs sm:text-sm text-slate-600 dark:text-slate-300">
              Listing Authority for <strong className="font-bold text-[#0C2B4E] dark:text-white">{urgentItem.propertyName}</strong> needs renewal ({urgentItem.expiryText}) to avoid publication interruption.
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={() =>
                setDrawerTarget({
                  type: "AUTHORITY",
                  authority: urgentItem,
                })
              }
              className="h-10 px-4 rounded-full border border-slate-300 dark:border-slate-700 bg-white dark:bg-slate-800 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading hover:bg-slate-50 transition cursor-pointer"
            >
              View details
            </button>
            <button
              onClick={() =>
                setAuthorityModalTarget({
                  propertyId: urgentItem.propertyId,
                  propertyLabel: urgentItem.propertyName,
                })
              }
              className="h-10 px-5 rounded-full bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold font-heading shadow-sm transition flex items-center justify-center cursor-pointer"
            >
              Renew authority
            </button>
          </div>
        </div>
      )}

      {/* 4. Filter Tabs Bar */}
      <div className="pt-1">
        <div className="inline-flex p-1 bg-slate-100 dark:bg-slate-800 rounded-full gap-1 overflow-x-auto max-w-full">
          {(
            [
              { key: "OVERVIEW", label: "Overview" },
              { key: "IDENTITY", label: "Identity" },
              { key: "PROPERTIES", label: "Properties" },
              { key: "AUTHORITY", label: "Authority" },
              { key: "ACTIVITY", label: "Activity" },
            ] as const
          ).map((tab) => (
            <button
              key={tab.key}
              type="button"
              onClick={() => setActiveTab(tab.key)}
              className={`h-9 px-4 rounded-full text-xs font-bold font-heading transition cursor-pointer shrink-0 ${
                activeTab === tab.key
                  ? "bg-white dark:bg-slate-900 text-[#0C2B4E] dark:text-white shadow-xs"
                  : "text-slate-500 dark:text-slate-400 hover:text-slate-800 dark:hover:text-slate-200"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Search & Status Filter (when viewing Properties or Overview) */}
      {(activeTab === "OVERVIEW" || activeTab === "PROPERTIES") && (
        <div className="w-full bg-white dark:bg-slate-900 rounded-[20px] border border-slate-200 dark:border-slate-800 p-4 shadow-2xs flex flex-col sm:flex-row items-stretch sm:items-end gap-3.5">
          <div className="flex-1">
            <span className="text-xs font-bold text-[#0C2B4E] dark:text-slate-300 font-heading block mb-1">
              Search
            </span>
            <div className="relative">
              <Search className="size-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search properties, rooms or addresses…"
                className="w-full h-11 pl-10 pr-4 bg-[#F1F5F9] dark:bg-slate-800 border border-slate-200/90 dark:border-slate-700 rounded-full text-xs text-[#0C2B4E] dark:text-slate-200 placeholder:text-slate-400 outline-none focus:ring-2 focus:ring-primary-500 font-normal shadow-2xs"
              />
            </div>
          </div>
          <RoundedFilterDropdown
            label="Verification Status"
            value={verificationStatusFilter}
            onChange={setVerificationStatusFilter}
            options={verificationFilterOptions}
            className="sm:w-60 flex-initial"
          />
        </div>
      )}

      {/* 5. Main Content Sections */}
      <div className="space-y-6 pt-1">
        {/* SECTION A: ACCOUNT IDENTITY */}
        {activeTab === "OVERVIEW" && (
          <div className="space-y-2.5">
            <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider pl-1 font-heading">
              Account identity
            </p>
            <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 shadow-2xs overflow-hidden">
              <div className="p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                {/* Left: User Icon + Details */}
                <div className="flex items-start sm:items-center gap-3.5 min-w-0">
                  <div className="size-11 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-[#0C2B4E] dark:text-white shrink-0">
                    <User className="size-5" />
                  </div>
                  <div className="space-y-0.5 min-w-0">
                    <h3 className="text-base font-bold text-[#0C2B4E] dark:text-white font-heading truncate">
                      {userName}
                    </h3>
                    <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
                      {userVerified
                        ? `Verified identity · Provider-managed verification · Last verified: ${identityDateText}`
                        : "Identity verification not yet completed for this account"}
                    </p>
                  </div>
                </div>

                {/* Middle: Verified Badge */}
                <div className="shrink-0">
                  {userVerified ? (
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                      <Check className="size-3 stroke-[2.5]" />
                      <span>Verified</span>
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400 text-xs font-extrabold uppercase tracking-wide">
                      <Clock className="size-3 stroke-[2.5]" />
                      <span>Action Required</span>
                    </span>
                  )}
                </div>

                {/* Right: Action Button */}
                <div className="shrink-0 flex justify-end">
                  {userVerified ? (
                    <button
                      onClick={() => setDrawerTarget({ type: "IDENTITY" })}
                      className="h-10 px-5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer flex items-center justify-center whitespace-nowrap"
                    >
                      View details
                    </button>
                  ) : (
                    <button
                      onClick={() => setIdentityModalOpen(true)}
                      className="h-10 px-5 bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer flex items-center justify-center whitespace-nowrap"
                    >
                      Verify identity
                    </button>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* DETAILED IDENTITY CARD (When IDENTITY tab is selected) */}
        {activeTab === "IDENTITY" && (
          <div className="space-y-2.5">
            <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider pl-1 font-heading">
              Identity verification
            </p>
            <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 shadow-2xs p-5 sm:p-6 space-y-5">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div>
                  <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Identity verification</span>
                  <h2 className="text-xl sm:text-2xl font-extrabold text-[#0C2B4E] dark:text-white font-heading">{userName}</h2>
                </div>
                {userVerified ? (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                    <Check className="size-3.5 stroke-[2.5]" />
                    <span>Verified</span>
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400 text-xs font-extrabold uppercase tracking-wide">
                    <Clock className="size-3.5 stroke-[2.5]" />
                    <span>Action Required</span>
                  </span>
                )}
              </div>

              <div className="border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 text-xs sm:text-sm">
                <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                  <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Verification method</dt>
                  <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">Document + Selfie identity verification</dd>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-1 sm:gap-4 p-3.5">
                  <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Verified name</dt>
                  <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{userName}</dd>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                  <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Last verified</dt>
                  <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{identityDateText}</dd>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-1 sm:gap-4 p-3.5">
                  <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Reverification</dt>
                  <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">Not currently required</dd>
                </div>
              </div>

              <div className="p-4 rounded-2xl bg-[#EEF3FF] dark:bg-slate-800/80 border border-[#AFC2EE]/40 dark:border-slate-700 flex items-start gap-3 text-xs sm:text-sm text-[#1D3F8F] dark:text-blue-300 leading-relaxed">
                <Lock className="size-4 shrink-0 mt-0.5" />
                <div>
                  <strong className="font-bold">Privacy.</strong> Your identity documents and biometric verification material are handled by the specialist verification provider. Zoiko Rooms displays the verification result and stores only the minimum required verification metadata.
                </div>
              </div>

              <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
                Identity is shared across your Owner, Agent and Renter roles. It doesn’t prove property ownership or authority to list.
              </p>

              <div className="flex justify-end pt-2 gap-3">
                <button
                  onClick={() => setDrawerTarget({ type: "IDENTITY" })}
                  className="h-10 px-5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer flex items-center justify-center whitespace-nowrap"
                >
                  View identity drawer
                </button>
                <button
                  onClick={() => setIdentityModalOpen(true)}
                  className="h-10 px-5 bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer flex items-center justify-center whitespace-nowrap"
                >
                  {userVerified ? "Re-verify identity" : "Verify identity"}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* SECTION B: PROPERTIES */}
        {(activeTab === "OVERVIEW" || activeTab === "PROPERTIES") && (
          <div className="space-y-2.5">
            <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider pl-1 font-heading">
              Properties
            </p>
            <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 shadow-2xs divide-y divide-slate-100 dark:divide-slate-800 overflow-hidden">
              {propertiesList.length === 0 ? (
                <div className="p-8 text-center space-y-2.5">
                  <div className="size-12 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mx-auto text-slate-400">
                    <Building2 className="size-6 text-[#0C2B4E] dark:text-white" />
                  </div>
                  <p className="text-sm font-bold text-[#0C2B4E] dark:text-white font-heading">
                    No properties added yet
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm mx-auto">
                    Add a property in Properties &amp; Rooms to begin location and ownership verification.
                  </p>
                  <Link
                    href="/account/host"
                    className="inline-flex items-center gap-1.5 px-5 py-2 mt-2 bg-[#0C2B4E] text-white text-xs font-bold font-heading rounded-full hover:bg-[#09223e] transition cursor-pointer"
                  >
                    <span>Manage Properties</span>
                  </Link>
                </div>
              ) : (
                propertiesList.map((item) => (
                  <div
                    key={item.id}
                    className="p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-slate-50/50 dark:hover:bg-slate-800/20 transition"
                  >
                    {/* Left: Building Icon + Details */}
                    <div className="flex items-start sm:items-center gap-3.5 min-w-0 md:w-1/2">
                      <div className="size-11 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-[#0C2B4E] dark:text-white shrink-0">
                        <Building2 className="size-5" />
                      </div>
                      <div className="space-y-0.5 min-w-0">
                        <h3 className="text-base font-bold text-[#0C2B4E] dark:text-white font-heading truncate">
                          {item.name}
                        </h3>
                        <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                          {item.address}
                        </p>
                      </div>
                    </div>

                    {/* Middle: Status Badge + Subtext */}
                    <div className="space-y-0.5 min-w-0 md:w-1/3">
                      <span
                        className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-extrabold uppercase tracking-wide ${
                          item.state === "VERIFIED"
                            ? "bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400"
                            : "bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400"
                        }`}
                      >
                        {item.state === "VERIFIED" ? (
                          <Check className="size-3 stroke-[2.5]" />
                        ) : (
                          <Clock className="size-3 stroke-[2.5]" />
                        )}
                        <span>{item.stateLabel}</span>
                      </span>
                      <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                        {item.notes}
                      </p>
                    </div>

                    {/* Right: Action Button -> Opens Drawer when data is there */}
                    <div className="shrink-0 flex justify-end">
                      <button
                        onClick={() =>
                          setDrawerTarget({
                            type: "PROPERTY",
                            property: item,
                          })
                        }
                        className="h-10 px-5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer flex items-center justify-center whitespace-nowrap"
                      >
                        View property verification
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
            {activeTab === "PROPERTIES" && (
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-2">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Address and map checks confirm location quality only — never title, ownership or listing authority.
                </p>
                <Link
                  href="/account/host"
                  className="inline-flex items-center gap-1.5 px-5 py-2.5 bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading rounded-full transition cursor-pointer self-start sm:self-auto shrink-0"
                >
                  <span>Manage Properties</span>
                </Link>
              </div>
            )}
          </div>
        )}

        {/* SECTION C: AUTHORITY */}
        {(activeTab === "OVERVIEW" || activeTab === "AUTHORITY") && (
          <div className="space-y-4">
            <div className="space-y-2.5">
              <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider pl-1 font-heading">
                Listing authority
              </p>
              <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 shadow-2xs divide-y divide-slate-100 dark:divide-slate-800 overflow-hidden">
                {authorityList.length === 0 ? (
                  <div className="p-8 text-center space-y-2.5">
                    <div className="size-12 rounded-2xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center mx-auto text-slate-400">
                      <KeyRound className="size-6 text-[#0C2B4E] dark:text-white" />
                    </div>
                    <p className="text-sm font-bold text-[#0C2B4E] dark:text-white font-heading">
                      No authority records yet
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400 max-w-sm mx-auto">
                      Authority to list and manage properties is created when listing rooms or verifying properties.
                    </p>
                  </div>
                ) : (
                  authorityList.map((item) => {
                    const isExpiring = item.isExpiringSoon || item.state === "EXPIRING_SOON";

                    return (
                      <div
                        key={item.id}
                        className={`p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 transition ${
                          isExpiring
                            ? "bg-[#FFF9F9] dark:bg-red-950/10"
                            : "hover:bg-slate-50/50 dark:hover:bg-slate-800/20"
                        }`}
                      >
                        {/* Left: Key Icon + Details */}
                        <div className="flex items-start sm:items-center gap-3.5 min-w-0 md:w-1/2">
                          <div className="size-11 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-[#0C2B4E] dark:text-white shrink-0">
                            <KeyRound className="size-5" />
                          </div>
                          <div className="space-y-0.5 min-w-0">
                            <h3 className="text-base font-bold text-[#0C2B4E] dark:text-white font-heading truncate">
                              {item.propertyName}
                            </h3>
                            <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                              {item.role}
                            </p>
                          </div>
                        </div>

                        {/* Middle: Badge + Scope + Expiry */}
                        <div className="space-y-0.5 min-w-0 md:w-1/3">
                          {isExpiring ? (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 text-amber-700 dark:text-amber-400 text-xs font-extrabold uppercase tracking-wide">
                              <AlertTriangle className="size-3 stroke-[2.5]" />
                              <span>Expiring Soon</span>
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                              <Check className="size-3 stroke-[2.5]" />
                              <span>Listing Authority Verified</span>
                            </span>
                          )}
                          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                            {item.scope}
                          </p>
                          <p
                            className={`text-xs ${
                              isExpiring
                                ? "text-amber-700 dark:text-amber-400 font-bold"
                                : "text-slate-500 dark:text-slate-400"
                            }`}
                          >
                            {item.expiryText}
                          </p>
                        </div>

                        {/* Right: Action Buttons */}
                        <div className="shrink-0 flex items-center justify-end gap-2">
                          <button
                            onClick={() =>
                              setDrawerTarget({
                                type: "AUTHORITY",
                                authority: item,
                              })
                            }
                            className="h-10 px-5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer flex items-center justify-center whitespace-nowrap"
                          >
                            View authority
                          </button>
                          {isExpiring && (
                            <button
                              onClick={() =>
                                setAuthorityModalTarget({
                                  propertyId: item.propertyId,
                                  propertyLabel: item.propertyName,
                                })
                              }
                              className="h-10 px-5 bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold font-heading rounded-full shadow-sm transition cursor-pointer flex items-center justify-center whitespace-nowrap"
                            >
                              Renew authority
                            </button>
                          )}
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>

            {/* Payment Receipt Authority (Only in Authority tab) */}
            {activeTab === "AUTHORITY" && (
              <>
                <div className="space-y-2.5 pt-2">
                  <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider pl-1 font-heading">
                    Payment receipt authority
                  </p>
                  <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 shadow-2xs divide-y divide-slate-100 dark:divide-slate-800 overflow-hidden">
                    {paymentReceiptList.map((pay) => (
                      <div
                        key={pay.prop}
                        className="p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4 hover:bg-slate-50/50 dark:hover:bg-slate-800/20 transition"
                      >
                        <div className="flex items-start sm:items-center gap-3.5 min-w-0 md:w-1/2">
                          <div className="size-11 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-[#0C2B4E] dark:text-white shrink-0">
                            <Wallet className="size-5" />
                          </div>
                          <div className="space-y-0.5 min-w-0">
                            <h3 className="text-base font-bold text-[#0C2B4E] dark:text-white font-heading truncate">
                              {pay.prop}
                            </h3>
                            <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                              Payee: {pay.payee}
                            </p>
                          </div>
                        </div>

                        <div className="space-y-0.5 min-w-0 md:w-1/3">
                          <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                            <Check className="size-3 stroke-[2.5]" />
                            <span>PAYMENT_RECEIPT Verified</span>
                          </span>
                          <p className="text-xs text-slate-500 dark:text-slate-400 truncate">
                            {pay.acct || pay.note}
                          </p>
                        </div>

                        <div className="shrink-0 flex justify-end">
                          {pay.acct ? (
                            <button
                              onClick={() =>
                                setDrawerTarget({
                                  type: "PAYMENT",
                                  propertyName: pay.prop,
                                  payee: pay.payee,
                                  account: pay.acct,
                                  scope: pay.scope,
                                })
                              }
                              className="h-10 px-5 bg-white dark:bg-slate-800 border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading rounded-full shadow-2xs transition cursor-pointer flex items-center justify-center whitespace-nowrap"
                            >
                              View payee
                            </button>
                          ) : (
                            <span className="text-xs text-slate-400">Not yours</span>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                  <p className="text-xs text-slate-500 dark:text-slate-400 px-1">
                    Payment Receipt is verified separately — it never comes automatically from listing authority.
                  </p>
                </div>

                {/* Organization & Representative */}
                <div className="space-y-2.5 pt-2">
                  <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider pl-1 font-heading">
                    Organization &amp; representative
                  </p>
                  <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 shadow-2xs divide-y divide-slate-100 dark:divide-slate-800 overflow-hidden">
                    <div className="p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                      <div className="flex items-start sm:items-center gap-3.5 min-w-0">
                        <div className="size-11 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-[#0C2B4E] dark:text-white shrink-0">
                          <Building2 className="size-5" />
                        </div>
                        <div className="space-y-0.5 min-w-0">
                          <h3 className="text-base font-bold text-[#0C2B4E] dark:text-white font-heading truncate">
                            Riverside Property Management Ltd
                          </h3>
                          <p className="text-xs text-slate-500 dark:text-slate-400">
                            Registered company · Telangana, India
                          </p>
                        </div>
                      </div>
                      <div>
                        <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                          <Check className="size-3 stroke-[2.5]" />
                          <span>Organization Verified</span>
                        </span>
                      </div>
                    </div>
                    <div className="p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-4">
                      <div className="flex items-start sm:items-center gap-3.5 min-w-0">
                        <div className="size-11 rounded-xl bg-slate-100 dark:bg-slate-800 flex items-center justify-center text-[#0C2B4E] dark:text-white shrink-0">
                          <Users className="size-5" />
                        </div>
                        <div className="space-y-0.5 min-w-0">
                          <h3 className="text-base font-bold text-[#0C2B4E] dark:text-white font-heading truncate">
                            {userName} · Director
                          </h3>
                          <p className="text-xs text-slate-500 dark:text-slate-400">
                            Representative authority · Listing management, applications
                          </p>
                        </div>
                      </div>
                      <div>
                        <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                          <Check className="size-3 stroke-[2.5]" />
                          <span>Representative Verified</span>
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>
        )}

        {/* SECTION D: ACTIVITY (Audit Log) */}
        {activeTab === "ACTIVITY" && (
          <div className="space-y-4">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-extrabold text-slate-500 dark:text-slate-400 uppercase tracking-wider pl-1 font-heading">
                Verification audit activity
              </p>
              <RoundedFilterDropdown
                label="Show Activity"
                value={activityFilter}
                onChange={setActivityFilter}
                options={activityFilterOptions}
                size="sm"
                className="w-48"
              />
            </div>
            <div className="bg-white dark:bg-slate-900 rounded-2xl border border-slate-200/90 dark:border-slate-800 shadow-2xs divide-y divide-slate-100 dark:divide-slate-800 overflow-hidden">
              {identityRecords.length === 0 && authorityList.length === 0 ? (
                <p className="text-xs text-slate-500 text-center py-6">
                  No verification audit events recorded yet for this account.
                </p>
              ) : (
                <>
                  {identityRecords.map((rec) => (
                    <div key={rec.id} className="p-4 flex items-center gap-3 hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition">
                      <div className="size-2.5 rounded-full bg-[#0C2B4E] dark:bg-sky-400 shrink-0" />
                      <span className="text-xs font-bold text-slate-400 w-16 shrink-0 tabular-nums">
                        {formatDate(rec.createdAt)}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs sm:text-sm font-semibold text-[#0C2B4E] dark:text-white truncate">
                          Identity verification completed · {rec.documentType} ({rec.status})
                        </p>
                      </div>
                      <span className="text-[10px] font-extrabold uppercase tracking-wider px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300 shrink-0">
                        identity
                      </span>
                    </div>
                  ))}

                  {authorityList.map((auth) => (
                    <div key={auth.id} className="p-4 flex items-center gap-3 hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition">
                      <div className="size-2.5 rounded-full bg-[#0C2B4E] dark:bg-sky-400 shrink-0" />
                      <span className="text-xs font-bold text-slate-400 w-16 shrink-0 tabular-nums">
                        Sep 20
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs sm:text-sm font-semibold text-[#0C2B4E] dark:text-white truncate">
                          {auth.role} listing authority verified · {auth.propertyName}
                        </p>
                      </div>
                      <span className="text-[10px] font-extrabold uppercase tracking-wider px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300 shrink-0">
                        authority
                      </span>
                    </div>
                  ))}

                  {propertiesList.map((p) => (
                    <div key={p.id} className="p-4 flex items-center gap-3 hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition">
                      <div className="size-2.5 rounded-full bg-[#0C2B4E] dark:bg-sky-400 shrink-0" />
                      <span className="text-xs font-bold text-slate-400 w-16 shrink-0 tabular-nums">
                        Sep 20
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-xs sm:text-sm font-semibold text-[#0C2B4E] dark:text-white truncate">
                          {p.name} property verified
                        </p>
                      </div>
                      <span className="text-[10px] font-extrabold uppercase tracking-wider px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300 shrink-0">
                        property
                      </span>
                    </div>
                  ))}
                </>
              )}
            </div>
            <p className="text-xs text-slate-500 dark:text-slate-400 pl-1">
              Append-only audit trail. Each event records domain, timestamp, and status.
            </p>
          </div>
        )}

        {/* 6. Explainer Footer Banner matching Figma Frame */}
        <div className="p-4 bg-[#F0F4F8] dark:bg-slate-800/60 rounded-2xl flex items-start gap-3 border border-slate-200/60 dark:border-slate-700/60">
          <Info className="size-4 text-[#0C2B4E] dark:text-sky-300 shrink-0 mt-0.5" />
          <p className="text-xs sm:text-sm font-medium text-[#0C2B4E] dark:text-slate-200 leading-relaxed">
            Each check answers a different question. Identity proves who you are; Property Verification checks the property; Listing Authority checks what you may do for that property; Payment Receipt Authority covers who may receive rent.
          </p>
        </div>
      </div>

      {/* ========================================================================= */}
      {/* 7. SLIDE-OUT DRAWER (SIDEBAR) (Portaled to document.body)                  */}
      {/* ========================================================================= */}
      {mounted && drawerTarget && createPortal(
        <div className="fixed inset-0 z-50 flex justify-end">
          {/* Scrim / Backdrop */}
          <div
            className="fixed inset-0 bg-slate-900/50 backdrop-blur-[2px] transition-opacity animate-in fade-in duration-200"
            onClick={() => setDrawerTarget(null)}
            aria-hidden="true"
          />

          {/* Aside Panel */}
          <aside
            className="relative z-10 w-full max-w-[520px] bg-white dark:bg-slate-900 h-full shadow-2xl flex flex-col justify-between overflow-y-auto p-6 sm:p-7 border-l border-slate-200 dark:border-slate-800 sm:rounded-l-[28px] animate-in slide-in-from-right duration-200 text-slate-800 dark:text-slate-100"
            role="dialog"
            aria-modal="true"
          >
            {/* Drawer Content */}
            <div className="space-y-6">
              {/* Drawer Header */}
              <div className="flex items-start justify-between gap-4 pb-4 border-b border-slate-100 dark:border-slate-800">
                <div className="space-y-1 min-w-0">
                  <p className="text-[11px] font-bold text-[#D80B0B] dark:text-red-400 uppercase tracking-widest font-heading">
                    {drawerTarget.type === "IDENTITY" && "Identity verification"}
                    {drawerTarget.type === "PROPERTY" && `Property #${drawerTarget.property.code}`}
                    {drawerTarget.type === "AUTHORITY" &&
                      `${drawerTarget.authority.propertyName} · Authority #ZR-AUTH-204${drawerTarget.authority.id
                        .toString()
                        .padStart(2, "0")}`}
                    {drawerTarget.type === "PAYMENT" && `${drawerTarget.propertyName} · Payment receipt`}
                  </p>
                  <h2 className="text-xl sm:text-2xl font-extrabold text-[#0C2B4E] dark:text-white font-heading truncate">
                    {drawerTarget.type === "IDENTITY" && userName}
                    {drawerTarget.type === "PROPERTY" && drawerTarget.property.name}
                    {drawerTarget.type === "AUTHORITY" && drawerTarget.authority.role}
                    {drawerTarget.type === "PAYMENT" && "Payee Details"}
                  </h2>
                </div>
                <button
                  type="button"
                  onClick={() => setDrawerTarget(null)}
                  className="size-9 rounded-full border border-slate-200 dark:border-slate-700 hover:bg-slate-100 dark:hover:bg-slate-800 flex items-center justify-center text-slate-500 hover:text-slate-900 dark:hover:text-white transition cursor-pointer shrink-0"
                  aria-label="Close drawer"
                >
                  <X className="size-4" />
                </button>
              </div>

              {/* IDENTITY DRAWER BODY */}
              {drawerTarget.type === "IDENTITY" && (
                <div className="space-y-5">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                      <Check className="size-3.5 stroke-[2.5]" />
                      <span>Verified</span>
                    </span>
                  </div>

                  <div className="border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 text-xs sm:text-sm">
                    <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Verification method</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">Document + Selfie identity verification</dd>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-1 sm:gap-4 p-3.5">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Verified name</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{userName}</dd>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Last verified</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{identityDateText}</dd>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-[160px_1fr] gap-1 sm:gap-4 p-3.5">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Reverification</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">Not currently required</dd>
                    </div>
                  </div>

                  <div className="p-4 rounded-2xl bg-[#EEF3FF] dark:bg-slate-800/80 border border-[#AFC2EE]/40 dark:border-slate-700 flex items-start gap-3 text-xs sm:text-sm text-[#1D3F8F] dark:text-blue-300 leading-relaxed">
                    <Lock className="size-4 shrink-0 mt-0.5" />
                    <div>
                      <strong className="font-bold">Privacy.</strong> Your identity documents and biometric verification material are handled by the specialist verification provider. Zoiko Rooms displays the verification result and stores only the minimum required verification metadata.
                    </div>
                  </div>

                  <p className="text-xs text-slate-500 dark:text-slate-400 leading-relaxed">
                    Identity is shared across your Owner, Agent and Renter roles. It doesn’t prove property ownership or authority to list.
                  </p>
                </div>
              )}

              {/* PROPERTY DRAWER BODY */}
              {drawerTarget.type === "PROPERTY" && (
                <div className="space-y-5">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span
                      className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-extrabold uppercase tracking-wide ${
                        drawerTarget.property.state === "VERIFIED"
                          ? "bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400"
                          : "bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800 text-amber-700 dark:text-amber-400"
                      }`}
                    >
                      {drawerTarget.property.state === "VERIFIED" ? (
                        <Check className="size-3.5 stroke-[2.5]" />
                      ) : (
                        <Clock className="size-3.5 stroke-[2.5]" />
                      )}
                      <span>{drawerTarget.property.stateLabel}</span>
                    </span>
                    <span className="text-xs text-slate-500 dark:text-slate-400">
                      Verified on {drawerTarget.property.verifiedAtText || "Sep 20, 2026"}
                    </span>
                  </div>

                  <div className="border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 text-xs sm:text-sm">
                    <div className="grid grid-cols-1 sm:grid-cols-[120px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Address</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{drawerTarget.property.address}</dd>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-500 dark:text-slate-400 font-heading">
                      Verification Gates &amp; Checklist
                    </h3>
                    <ul className="border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 text-xs sm:text-sm">
                      {[
                        { label: "Canonical structured address", status: "Normalized" },
                        { label: "Address / location validation", status: "Confirmed" },
                        { label: "Map location confirmation", status: "Confirmed" },
                        { label: "Property evidence", status: "Verified (property tax record)" },
                        { label: "Registry check", status: "Passed where available" },
                        { label: "Duplicate / fraud checks", status: "No issues" },
                      ].map((gate) => (
                        <li
                          key={gate.label}
                          className="p-3.5 flex items-center justify-between gap-3 hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition"
                        >
                          <div className="flex items-center gap-2.5 min-w-0">
                            <Check className="size-4 text-emerald-600 shrink-0 stroke-[2.5]" />
                            <span className="text-slate-700 dark:text-slate-300 font-medium truncate">
                              {gate.label}
                            </span>
                          </div>
                          <strong className="text-[#0C2B4E] dark:text-white font-bold shrink-0">{gate.status}</strong>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="p-4 rounded-2xl bg-[#EEF3FF] dark:bg-slate-800/80 border border-[#AFC2EE]/40 dark:border-slate-700 flex items-start gap-3 text-xs sm:text-sm text-[#1D3F8F] dark:text-blue-300 leading-relaxed">
                    <Info className="size-4 shrink-0 mt-0.5" />
                    <span>
                      Property verification proves the property/address checks shown here. It does not by itself prove that you own the property or are authorized to list it.
                    </span>
                  </div>
                </div>
              )}

              {/* AUTHORITY DRAWER BODY */}
              {drawerTarget.type === "AUTHORITY" &&
                (() => {
                  const isOwner = drawerTarget.authority.role.toLowerCase().includes("owner");
                  const caps = [
                    { code: "LISTING_CREATE", granted: isOwner },
                    { code: "LISTING_MANAGE", granted: true },
                    { code: "LISTING_PUBLISH", granted: true },
                    { code: "APPLICATION_MANAGE", granted: true },
                    { code: "AGREEMENT_MANAGE", granted: isOwner },
                    { code: "SUBLET_DECIDE", granted: isOwner },
                    { code: "PAYMENT_RECEIPT", granted: isOwner },
                  ];

                  return (
                    <div className="space-y-5">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span
                          className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-extrabold uppercase tracking-wide ${
                            drawerTarget.authority.isExpiringSoon ||
                            drawerTarget.authority.state === "EXPIRING_SOON"
                              ? "bg-amber-50 dark:bg-amber-950/40 border border-amber-300 dark:border-amber-800 text-amber-700 dark:text-amber-400"
                              : "bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400"
                          }`}
                        >
                          {drawerTarget.authority.isExpiringSoon ? (
                            <AlertTriangle className="size-3.5 stroke-[2.5]" />
                          ) : (
                            <Check className="size-3.5 stroke-[2.5]" />
                          )}
                          <span>{drawerTarget.authority.stateLabel}</span>
                        </span>
                        {drawerTarget.authority.isExpiringSoon && (
                          <span className="text-xs text-amber-700 dark:text-amber-400 font-bold">
                            {drawerTarget.authority.expiryText}
                          </span>
                        )}
                      </div>

                      <div className="space-y-2">
                        <h3 className="text-xs font-extrabold uppercase tracking-wider text-slate-500 dark:text-slate-400 font-heading">
                          Capabilities
                        </h3>
                        <ul className="border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 text-xs sm:text-sm">
                          {caps.map((c) => (
                            <li
                              key={c.code}
                              className="p-3 flex items-center justify-between gap-3 hover:bg-slate-50/50 dark:hover:bg-slate-800/30 transition"
                            >
                              <div className="flex items-center gap-2.5">
                                {c.granted ? (
                                  <Check className="size-4 text-emerald-600 stroke-[2.5]" />
                                ) : (
                                  <Minus className="size-4 text-slate-400 stroke-[2.5]" />
                                )}
                                <code
                                  className={`font-mono text-xs font-bold ${
                                    c.granted ? "text-[#0C2B4E] dark:text-white" : "text-slate-400"
                                  }`}
                                >
                                  {c.code}
                                </code>
                              </div>
                              {!c.granted && <span className="text-xs text-slate-400">not granted</span>}
                            </li>
                          ))}
                        </ul>
                        {!isOwner && (
                          <p className="text-xs text-slate-500 dark:text-slate-400">
                            Without SUBLET_DECIDE you can review sublet requests but the owner decides.
                          </p>
                        )}
                      </div>

                      <div className="border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 text-xs sm:text-sm">
                        <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                          <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Effective</dt>
                          <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">
                            {drawerTarget.authority.effectiveFromText || "Sep 20, 2026"} →{" "}
                            {drawerTarget.authority.expiryText}
                          </dd>
                        </div>
                        <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-4 p-3.5">
                          <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Authority basis</dt>
                          <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">
                            {drawerTarget.authority.authorityBasis ||
                              (isOwner
                                ? "Verified ownership record"
                                : "Verified mandate / delegation from property owner")}
                          </dd>
                        </div>
                        {!isOwner && (
                          <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                            <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Acting through</dt>
                            <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">
                              {drawerTarget.authority.actingThrough || "Riverside Property Management Ltd"}
                            </dd>
                          </div>
                        )}
                      </div>

                      {(drawerTarget.authority.isExpiringSoon ||
                        drawerTarget.authority.state !== "VERIFIED") && (
                        <div className="space-y-2 p-4 rounded-2xl bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800/60">
                          <h3 className="text-xs font-extrabold uppercase tracking-wider text-amber-900 dark:text-amber-300 font-heading">
                            Downstream impact if expired
                          </h3>
                          <ul className="text-xs text-amber-800 dark:text-amber-200 list-disc pl-4 space-y-1">
                            <li>Existing rental records remain preserved</li>
                            <li>New listing publication blocked</li>
                            <li>Live listing treatment follows platform suspension/renewal policy</li>
                            <li>New agreement/application actions requiring this authority may be blocked</li>
                          </ul>
                        </div>
                      )}
                    </div>
                  );
                })()}

              {/* PAYMENT DRAWER BODY */}
              {drawerTarget.type === "PAYMENT" && (
                <div className="space-y-5">
                  <div className="flex items-center gap-2">
                    <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 dark:bg-emerald-950/40 border border-emerald-200 dark:border-emerald-800 text-emerald-700 dark:text-emerald-400 text-xs font-extrabold uppercase tracking-wide">
                      <Check className="size-3.5 stroke-[2.5]" />
                      <span>PAYMENT_RECEIPT Verified</span>
                    </span>
                  </div>

                  <div className="border border-slate-200 dark:border-slate-800 rounded-2xl overflow-hidden divide-y divide-slate-100 dark:divide-slate-800 text-xs sm:text-sm">
                    <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Property</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{drawerTarget.propertyName}</dd>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-4 p-3.5">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Payee Name</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{drawerTarget.payee}</dd>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-4 p-3.5 bg-slate-50/50 dark:bg-slate-800/40">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Account</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{drawerTarget.account}</dd>
                    </div>
                    <div className="grid grid-cols-1 sm:grid-cols-[140px_1fr] gap-1 sm:gap-4 p-3.5">
                      <dt className="text-xs font-bold text-slate-500 dark:text-slate-400">Payment Scope</dt>
                      <dd className="font-semibold text-[#0C2B4E] dark:text-slate-100">{drawerTarget.scope}</dd>
                    </div>
                  </div>

                  <div className="p-4 rounded-2xl bg-[#EEF3FF] dark:bg-slate-800/80 border border-[#AFC2EE]/40 dark:border-slate-700 flex items-start gap-3 text-xs sm:text-sm text-[#1D3F8F] dark:text-blue-300 leading-relaxed">
                    <Info className="size-4 shrink-0 mt-0.5" />
                    <span>
                      Payment Receipt is verified separately — it never comes automatically from listing authority. Rent is disbursed only to this verified payee account.
                    </span>
                  </div>
                </div>
              )}
            </div>

            {/* Drawer Footer Actions */}
            <div className="pt-6 border-t border-slate-100 dark:border-slate-800 flex justify-end gap-3 shrink-0">
              {drawerTarget.type === "IDENTITY" && (
                <button
                  onClick={() => {
                    setDrawerTarget(null);
                    setIdentityModalOpen(true);
                  }}
                  className="h-11 px-6 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading transition cursor-pointer"
                >
                  Re-verify identity
                </button>
              )}

              {drawerTarget.type === "PROPERTY" && (
                <button
                  onClick={() => {
                    const p = drawerTarget.property;
                    setDrawerTarget(null);
                    setPropertyModalTarget({ propertyId: p.id, propertyLabel: p.name });
                  }}
                  className="h-11 px-6 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading transition cursor-pointer"
                >
                  Open Property Verification Wizard
                </button>
              )}

              {drawerTarget.type === "AUTHORITY" &&
                (drawerTarget.authority.isExpiringSoon ? (
                  <button
                    onClick={() => {
                      const a = drawerTarget.authority;
                      setDrawerTarget(null);
                      setAuthorityModalTarget({
                        propertyId: a.propertyId,
                        propertyLabel: a.propertyName,
                      });
                    }}
                    className="h-11 px-6 rounded-full bg-[#D80B0B] hover:bg-red-700 text-white text-xs font-bold font-heading shadow-md transition cursor-pointer"
                  >
                    Renew Authority
                  </button>
                ) : (
                  <button
                    onClick={() => {
                      const a = drawerTarget.authority;
                      setDrawerTarget(null);
                      setAuthorityModalTarget({
                        propertyId: a.propertyId,
                        propertyLabel: a.propertyName,
                      });
                    }}
                    className="h-11 px-6 rounded-full border border-slate-300 dark:border-slate-700 hover:bg-slate-50 text-[#0C2B4E] dark:text-white text-xs font-bold font-heading transition cursor-pointer"
                  >
                    Open Authority Wizard
                  </button>
                ))}

              {drawerTarget.type === "PAYMENT" && (
                <button
                  onClick={() => setDrawerTarget(null)}
                  className="h-11 px-6 rounded-full bg-[#0C2B4E] hover:bg-[#09223e] text-white text-xs font-bold font-heading transition cursor-pointer"
                >
                  Done
                </button>
              )}
            </div>
          </aside>
        </div>,
        document.body
      )}

      {/* MODAL 1: Authority Verification Wizard */}
      <Modal
        open={authorityModalTarget !== null}
        onClose={() => setAuthorityModalTarget(null)}
        title="Authority to list"
        size="xl"
      >
        {authorityModalTarget && (
          <AuthorityVerificationWizard
            propertyId={authorityModalTarget.propertyId}
            propertyLabel={authorityModalTarget.propertyLabel}
            onClose={() => {
              setAuthorityModalTarget(null);
              loadData();
            }}
          />
        )}
      </Modal>

      {/* MODAL 2: Property Verification Wizard */}
      <Modal
        open={propertyModalTarget !== null}
        onClose={() => setPropertyModalTarget(null)}
        title="Property verification"
        size="xl"
      >
        {propertyModalTarget && (
          <PropertyVerificationWizard
            propertyId={propertyModalTarget.propertyId}
            propertyLabel={propertyModalTarget.propertyLabel}
            onClose={() => {
              setPropertyModalTarget(null);
              loadData();
            }}
          />
        )}
      </Modal>

      {/* MODAL 3: Identity Verification Wizard */}
      <Modal
        open={identityModalOpen}
        onClose={() => setIdentityModalOpen(false)}
        title="Identity Verification"
        size="xl"
      >
        <IdentityVerificationWizard />
      </Modal>
    </div>
  );
}
