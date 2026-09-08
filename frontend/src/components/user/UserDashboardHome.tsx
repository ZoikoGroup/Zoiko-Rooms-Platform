"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  Building2,
  ClipboardList,
  CreditCard,
  DoorOpen,
  Repeat,
  Search,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { identityStatusLabel, identityStatusTone } from "@/lib/status";
import {
  listHostedProperties,
  listOccupancies,
  listRentalApplications,
  listSubletRequests,
  listUserPayments,
} from "@/lib/user-api";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Card } from "@/components/user/ui";

const initialCounts = { applications: 0, rentals: 0, sublets: 0, properties: 0, payments: 0 };

export function UserDashboardHome() {
  const { user, identityStatus, identityVerified } = useUserSession();
  const [counts, setCounts] = useState(initialCounts);

  useEffect(() => {
    Promise.all([
      listRentalApplications().catch(() => []),
      listOccupancies().catch(() => []),
      listSubletRequests().catch(() => []),
      listHostedProperties().catch(() => []),
      listUserPayments().catch(() => []),
    ]).then(([applications, rentals, sublets, properties, payments]) =>
      setCounts({
        applications: applications.length,
        rentals: rentals.length,
        sublets: sublets.length,
        properties: properties.length,
        payments: payments.length,
      })
    );
  }, []);

  const shortcuts = [
    { href: "/account/applications", label: "My Applications", icon: ClipboardList, count: counts.applications },
    { href: "/account/rentals", label: "My Rentals", icon: DoorOpen, count: counts.rentals },
    { href: "/account/sublets", label: "Sublet Requests", icon: Repeat, count: counts.sublets },
    { href: "/account/host", label: "My Properties", icon: Building2, count: counts.properties },
    { href: "/account/payments", label: "Payments", icon: CreditCard, count: counts.payments },
  ];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">
          Welcome{user?.fullName ? `, ${user.fullName.split(" ")[0]}` : ""}
        </h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Rent a verified private room, or host one of your own — both from this dashboard.
        </p>
      </div>

      {!identityVerified && (
        <Card className="!bg-amber-50 !ring-amber-200 dark:!bg-amber-500/10 dark:!ring-amber-500/20">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" />
              <div>
                <p className="text-sm font-semibold text-amber-800 dark:text-amber-300">
                  {identityStatusLabel[identityStatus]}
                </p>
                <p className="mt-0.5 text-xs text-amber-700 dark:text-amber-400">
                  Applying to rent a room and publishing a listing both need a verified identity.
                </p>
              </div>
            </div>
            <Link href="/account/identity" className="shrink-0">
              <Button size="sm">Verify identity</Button>
            </Link>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Link
          href="/account/rent"
          className="group relative overflow-hidden rounded-3xl bg-primary-900 p-7 text-white shadow-xl shadow-primary-900/25 transition-all duration-300 hover:-translate-y-1 hover:shadow-2xl"
        >
          <div className="bg-noise pointer-events-none absolute inset-0 opacity-20" />
          <div className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-primary-400/20 blur-3xl" />
          <div className="relative">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/10 ring-1 ring-white/20">
              <Search className="h-6 w-6" />
            </span>
            <h2 className="mt-5 font-heading text-xl font-extrabold">Find a Room</h2>
            <p className="mt-1.5 max-w-sm text-sm text-primary-200">
              Browse verified private rooms available for 30+ night stays and apply to the one you want.
            </p>
            <span className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-white transition-transform duration-300 group-hover:translate-x-1">
              Browse rooms <ArrowRight className="h-4 w-4" />
            </span>
          </div>
        </Link>

        <Link
          href="/account/host"
          className="group relative overflow-hidden rounded-3xl bg-accent-600 p-7 text-white shadow-xl shadow-accent-700/25 transition-all duration-300 hover:-translate-y-1 hover:shadow-2xl"
        >
          <div className="bg-noise pointer-events-none absolute inset-0 opacity-20" />
          <div className="pointer-events-none absolute -right-10 -top-10 h-40 w-40 rounded-full bg-white/15 blur-3xl" />
          <div className="relative">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white/15 ring-1 ring-white/25">
              <Building2 className="h-6 w-6" />
            </span>
            <h2 className="mt-5 font-heading text-xl font-extrabold">Become a Host</h2>
            <p className="mt-1.5 max-w-sm text-sm text-accent-50">
              Add your property, list a room, and publish it once every compliance check passes.
            </p>
            <span className="mt-5 inline-flex items-center gap-2 text-sm font-semibold text-white transition-transform duration-300 group-hover:translate-x-1">
              Manage your property <ArrowRight className="h-4 w-4" />
            </span>
          </div>
        </Link>
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        {shortcuts.map(({ href, label, icon: Icon, count }, i) => (
          <Link
            key={href}
            href={href}
            className="animate-fade-up group flex items-center justify-between gap-3 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 transition-all duration-300 hover:-translate-y-1 hover:shadow-lg dark:bg-slate-900 dark:ring-white/10"
            style={{ animationDelay: `${i * 0.06}s` }}
          >
            <span className="flex items-center gap-3">
              <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary-50 text-primary-700 transition-transform duration-300 group-hover:scale-110 group-hover:bg-primary-700 group-hover:text-white dark:bg-primary-500/10 dark:text-primary-300">
                <Icon className="h-5 w-5" />
              </span>
              <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">{label}</span>
            </span>
            <Badge tone={count > 0 ? "primary" : "neutral"}>{count}</Badge>
          </Link>
        ))}

        <Link
          href="/account/identity"
          className="animate-fade-up group flex items-center justify-between gap-3 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 transition-all duration-300 hover:-translate-y-1 hover:shadow-lg dark:bg-slate-900 dark:ring-white/10"
          style={{ animationDelay: "0.3s" }}
        >
          <span className="flex items-center gap-3">
            <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary-50 text-primary-700 transition-transform duration-300 group-hover:scale-110 group-hover:bg-primary-700 group-hover:text-white dark:bg-primary-500/10 dark:text-primary-300">
              <ShieldCheck className="h-5 w-5" />
            </span>
            <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">Identity Verification</span>
          </span>
          <Badge tone={identityStatusTone[identityStatus]}>{identityStatusLabel[identityStatus]}</Badge>
        </Link>
      </div>
    </div>
  );
}
