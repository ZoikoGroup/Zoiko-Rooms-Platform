"use client";

import { useEffect, useState, type FormEvent } from "react";
import { BadgeCheck, Bell, Lock, Mail, Phone, User } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { identityStatusLabel, identityStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";
import { changeUserPassword, updateUserProfile } from "@/lib/user-auth";
import { errorMessage } from "@/lib/user-api";
import { getNotificationPreferences, updateNotificationPreferences, USER_NOTIFICATIONS_BASE } from "@/lib/notifications";
import { NotificationCategory, NotificationPreference } from "@/lib/types";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Card, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";

// Section 11 gap: DISPUTES_AND_SAFETY is deliberately absent here -- the
// backend (models/notification.py:NOTIFICATION_OPTABLE_CATEGORIES) silently
// drops it from any update anyway, so it's never offered as a toggle in the
// first place.
const OPTABLE_CATEGORIES: { value: NotificationCategory; label: string; hint: string }[] = [
  { value: "PAYMENTS", label: "Payments & refunds", hint: "Payment confirmations, payouts, refunds, deposit updates." },
  { value: "LEASING", label: "Applications & agreements", hint: "Application, offer, agreement and listing updates." },
  { value: "OCCUPANCY", label: "Tenancy & occupancy", hint: "Move-in/out, termination cases, sublets, host visits." },
];

function minutesToTimeInput(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24;
  const m = minutes % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function timeInputToMinutes(value: string): number {
  const [h, m] = value.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

export function ProfileManager() {
  const { user, identityStatus, refreshUser } = useUserSession();
  const { toast, showToast } = useToast();

  const [fullName, setFullName] = useState(user?.fullName ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileError, setProfileError] = useState("");

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordError, setPasswordError] = useState("");

  const [prefs, setPrefs] = useState<NotificationPreference | null>(null);
  const [savingPrefs, setSavingPrefs] = useState(false);
  const [prefsError, setPrefsError] = useState("");

  useEffect(() => {
    setFullName(user?.fullName ?? "");
    setPhone(user?.phone ?? "");
  }, [user]);

  useEffect(() => {
    getNotificationPreferences(USER_NOTIFICATIONS_BASE).then(setPrefs).catch(() => {});
  }, []);

  function toggleCategory(category: NotificationCategory) {
    setPrefs((prev) => {
      if (!prev) return prev;
      const optedOut = prev.optedOutCategories.includes(category)
        ? prev.optedOutCategories.filter((c) => c !== category)
        : [...prev.optedOutCategories, category];
      return { ...prev, optedOutCategories: optedOut };
    });
  }

  async function handleSavePreferences() {
    if (!prefs) return;
    setPrefsError("");
    setSavingPrefs(true);
    try {
      const updated = await updateNotificationPreferences(USER_NOTIFICATIONS_BASE, prefs);
      setPrefs(updated);
      showToast("Notification preferences saved.");
    } catch (err) {
      setPrefsError(errorMessage(err, "Could not save your notification preferences."));
    } finally {
      setSavingPrefs(false);
    }
  }

  async function handleProfileSave(e: FormEvent) {
    e.preventDefault();
    if (!fullName.trim()) {
      setProfileError("Your full name cannot be empty.");
      return;
    }
    setProfileError("");
    setSavingProfile(true);
    try {
      await updateUserProfile(fullName.trim(), phone.trim());
      await refreshUser();
      showToast("Profile updated.");
    } catch (err) {
      setProfileError(errorMessage(err, "Could not update your profile."));
    } finally {
      setSavingProfile(false);
    }
  }

  async function handlePasswordChange(e: FormEvent) {
    e.preventDefault();
    if (newPassword.length < 8) {
      setPasswordError("Your new password must be at least 8 characters.");
      return;
    }
    if (newPassword !== confirmPassword) {
      setPasswordError("The two new passwords do not match.");
      return;
    }
    setPasswordError("");
    setSavingPassword(true);
    try {
      await changeUserPassword(currentPassword, newPassword);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      showToast("Password changed.");
    } catch (err) {
      setPasswordError(errorMessage(err, "Could not change your password."));
    } finally {
      setSavingPassword(false);
    }
  }

  return (
    <div className="space-y-5">
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-700 font-heading text-lg font-extrabold text-white">
              {(user?.fullName || user?.email || "U")[0].toUpperCase()}
            </span>
            <div>
              <p className="font-heading text-base font-bold text-primary-900 dark:text-white">
                {user?.fullName || "Your account"}
              </p>
              <p className="flex items-center gap-1.5 text-xs text-slate-400">
                <Mail className="h-3 w-3" /> {user?.email}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={identityStatusTone[identityStatus]} dot>
              <BadgeCheck className="h-3.5 w-3.5" /> {identityStatusLabel[identityStatus]}
            </Badge>
            {user?.partyId !== null && user?.partyId !== undefined && (
              <Badge tone="primary">Party #{user.partyId}</Badge>
            )}
          </div>
        </div>
        {user?.createdAt && (
          <p className="mt-4 border-t border-slate-100 pt-3 text-xs text-slate-400 dark:border-white/10">
            Member since {formatDate(user.createdAt)}
          </p>
        )}
      </Card>

      <Card>
        <SectionHeading title="Personal details" subtitle="Your name and phone number as hosts and Zoiko will see them." />
        <form onSubmit={handleProfileSave} className="mt-5 space-y-4">
          <Field label="Full name">
            <div className="relative">
              <User className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                className={`${inputClass} pl-10`}
                autoComplete="name"
              />
            </div>
          </Field>

          <Field label="Phone">
            <div className="relative">
              <Phone className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+91 98200 11223"
                className={`${inputClass} pl-10`}
                autoComplete="tel"
              />
            </div>
          </Field>

          <Field label="Email" hint="Your email is the identifier for your account and cannot be changed here.">
            <input value={user?.email ?? ""} disabled className={`${inputClass} opacity-60`} />
          </Field>

          {profileError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {profileError}
            </p>
          )}

          <Button type="submit" loading={savingProfile}>
            Save changes
          </Button>
        </form>
      </Card>

      <Card>
        <SectionHeading title="Change password" subtitle="Use at least 8 characters." />
        <form onSubmit={handlePasswordChange} className="mt-5 space-y-4">
          <Field label="Current password">
            <div className="relative">
              <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className={`${inputClass} pl-10`}
                autoComplete="current-password"
              />
            </div>
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="New password">
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className={inputClass}
                autoComplete="new-password"
              />
            </Field>
            <Field label="Confirm new password">
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                className={inputClass}
                autoComplete="new-password"
              />
            </Field>
          </div>

          {passwordError && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {passwordError}
            </p>
          )}

          <Button type="submit" variant="outline" loading={savingPassword}>
            Update password
          </Button>
        </form>
      </Card>

      {prefs && (
        <Card>
          <SectionHeading
            title="Notification preferences"
            subtitle="Choose which kinds of updates you receive, and set quiet hours for non-urgent ones."
          />
          <div className="mt-5 space-y-3">
            {OPTABLE_CATEGORIES.map((cat) => {
              const optedOut = prefs.optedOutCategories.includes(cat.value);
              return (
                <label
                  key={cat.value}
                  className="flex cursor-pointer items-start justify-between gap-4 rounded-xl border border-slate-100 p-3.5 dark:border-white/10"
                >
                  <div>
                    <p className="text-sm font-semibold text-primary-900 dark:text-white">{cat.label}</p>
                    <p className="text-xs text-slate-400">{cat.hint}</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={!optedOut}
                    onChange={() => toggleCategory(cat.value)}
                    className="mt-1 h-4 w-4 shrink-0 accent-accent-600"
                  />
                </label>
              );
            })}
            <p className="text-xs text-slate-400">
              Safety and dispute notifications (habitability issues, disputes, identity/screening decisions) always
              reach you and cannot be turned off.
            </p>
          </div>

          <div className="mt-5 border-t border-slate-100 pt-4 dark:border-white/10">
            <label className="flex items-center justify-between gap-4">
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">Quiet hours</p>
                <p className="text-xs text-slate-400">
                  Pause routine notifications during this window (UTC). Urgent safety/dispute notifications still
                  come through.
                </p>
              </div>
              <input
                type="checkbox"
                checked={prefs.quietHoursEnabled}
                onChange={(e) => setPrefs((p) => (p ? { ...p, quietHoursEnabled: e.target.checked } : p))}
                className="h-4 w-4 shrink-0 accent-accent-600"
              />
            </label>
            {prefs.quietHoursEnabled && (
              <div className="mt-3 grid grid-cols-2 gap-4">
                <Field label="From (UTC)">
                  <input
                    type="time"
                    value={minutesToTimeInput(prefs.quietHoursStartMinute)}
                    onChange={(e) =>
                      setPrefs((p) => (p ? { ...p, quietHoursStartMinute: timeInputToMinutes(e.target.value) } : p))
                    }
                    className={inputClass}
                  />
                </Field>
                <Field label="Until (UTC)">
                  <input
                    type="time"
                    value={minutesToTimeInput(prefs.quietHoursEndMinute)}
                    onChange={(e) =>
                      setPrefs((p) => (p ? { ...p, quietHoursEndMinute: timeInputToMinutes(e.target.value) } : p))
                    }
                    className={inputClass}
                  />
                </Field>
              </div>
            )}
          </div>

          {prefsError && (
            <p className="mt-4 rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {prefsError}
            </p>
          )}

          <Button className="mt-5" onClick={handleSavePreferences} loading={savingPrefs}>
            <Bell className="h-4 w-4" /> Save preferences
          </Button>
        </Card>
      )}

      <Toast toast={toast} />
    </div>
  );
}
