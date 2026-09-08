"use client";

import { useEffect, useState, type FormEvent } from "react";
import { BadgeCheck, Lock, Mail, Phone, User } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { identityStatusLabel, identityStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";
import { changeUserPassword, updateUserProfile } from "@/lib/user-auth";
import { errorMessage } from "@/lib/user-api";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Card, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";

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

  useEffect(() => {
    setFullName(user?.fullName ?? "");
    setPhone(user?.phone ?? "");
  }, [user]);

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

      <Toast toast={toast} />
    </div>
  );
}
