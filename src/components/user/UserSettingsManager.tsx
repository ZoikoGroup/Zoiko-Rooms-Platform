"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Bell,
  Camera,
  CheckCircle2,
  Download,
  Lock,
  LogOut,
  Mail,
  Settings,
  Shield,
  SlidersHorizontal,
  Trash2,
  User,
} from "lucide-react";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Toast, useToast } from "@/components/user/ui";
import { Modal } from "@/components/ui/Modal";
import { Loader } from "@/components/ui/Loader";
import { PhoneInputWithCountry } from "@/components/ui/PhoneInputWithCountry";
import { changeUserPassword, updateUserProfile } from "@/lib/user-auth";
import {
  USER_NOTIFICATIONS_BASE,
  getNotificationPreferences,
  updateNotificationPreferences,
} from "@/lib/notifications";
import {
  closeAccount,
  dataExportUrl,
  errorMessage,
  getAccountClosureCheck,
  getPendingEmailChange,
  getUserPreferences,
  profilePhotoUrl,
  removeProfilePhoto,
  requestEmailChange,
  signOutOtherSessions,
  updateUserPreferences,
  uploadProfilePhoto,
} from "@/lib/user-api";
import type { AccountClosureCheck, NotificationCategory, NotificationPreference, UserPreferences } from "@/lib/types";
import { cn } from "@/lib/utils";
import { applyUserPreferences } from "@/lib/display-preferences";

export type SettingsSection = "profile" | "security" | "notifications" | "preferences" | "privacy" | "account";
export type UserRoleMode = "host" | "renter";

const SECTIONS: Array<{ id: SettingsSection; label: string; icon: typeof User }> = [
  { id: "profile", label: "Profile", icon: User },
  { id: "security", label: "Sign-in & security", icon: Shield },
  { id: "notifications", label: "Notifications", icon: Bell },
  { id: "preferences", label: "Preferences", icon: SlidersHorizontal },
  { id: "privacy", label: "Privacy & data", icon: Lock },
  { id: "account", label: "Account", icon: Settings },
];

// Categories a user can switch off; the rest are always delivered.
const OPTABLE: Array<{ key: NotificationCategory; title: string; description: string }> = [
  { key: "LEASING", title: "Applications, offers & agreements", description: "New applications, offers, counter-offers, signatures and messages." },
  { key: "OCCUPANCY", title: "Tenancy", description: "Move-in and move-out steps, notices, change requests and sublets." },
  { key: "PAYMENTS", title: "Rent & payments", description: "Rent due reminders, receipts, confirmations and deposit updates." },
];
const ALWAYS_ON: Array<{ title: string; description: string }> = [
  { title: "Disputes & safety", description: "Disputes, safety reports and identity checks." },
  { title: "Account & support", description: "Replies to your support requests and account security." },
];

const LANGUAGES: Array<[UserPreferences["language"], string]> = [
  ["en-GB", "English (UK)"],
  ["en-US", "English (US)"],
];
const REGIONS: Array<[UserPreferences["region"], string]> = [
  ["GB", "United Kingdom"],
  ["US", "United States"],
  ["IN", "India"],
];
const TIME_ZONES = ["Europe/London", "Asia/Kolkata", "America/New_York", "America/Chicago", "America/Los_Angeles"];

const cardCls = "space-y-4 rounded-2xl border border-neutral-200 bg-white p-5 shadow-xs dark:border-neutral-800 dark:bg-neutral-900";
const inputCls =
  "mt-1 w-full rounded-xl border border-neutral-300 bg-white p-2.5 text-sm outline-none focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-900 dark:text-white";
const selectCls =
  "mt-1 w-full rounded-full border border-neutral-300 bg-white px-4 py-2.5 text-sm outline-none cursor-pointer focus:border-primary-700 dark:border-neutral-700 dark:bg-neutral-900 dark:text-white";
const primaryBtn = "rounded-xl bg-primary-900 px-4 py-2 text-xs font-bold text-white hover:bg-primary-800 disabled:opacity-50";
const secondaryBtn =
  "rounded-xl border border-neutral-300 bg-white px-4 py-2 text-xs font-bold text-neutral-800 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200";

function minutesToTime(m: number) {
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}
function timeToMinutes(t: string) {
  const [h, m] = t.split(":").map(Number);
  return (h || 0) * 60 + (m || 0);
}

function Toggle({ checked, onChange, disabled, label }: { checked: boolean; onChange: (v: boolean) => void; disabled?: boolean; label: string }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition disabled:opacity-60",
        checked ? "bg-primary-800" : "bg-neutral-300 dark:bg-neutral-700",
      )}
    >
      <span className={cn("inline-block h-5 w-5 rounded-full bg-white shadow transition", checked ? "translate-x-5" : "translate-x-0.5")} />
    </button>
  );
}

export function UserSettingsManager({
  defaultSection = "profile",
}: {
  defaultSection?: SettingsSection;
  /** Kept for the host settings route; the sections are the same for both. */
  initialRole?: UserRoleMode;
}) {
  const router = useRouter();
  const { user, identityVerified, refreshUser } = useUserSession();
  const { toast, showToast } = useToast();
  const [section, setSection] = useState<SettingsSection>(defaultSection);

  // Profile
  const [displayName, setDisplayName] = useState(user?.fullName ?? "");
  const [phone, setPhone] = useState(user?.phone ?? "");
  const [phoneError, setPhoneError] = useState<string | undefined>();
  const [savingProfile, setSavingProfile] = useState(false);
  const [photoBusy, setPhotoBusy] = useState(false);
  const [pendingEmail, setPendingEmail] = useState<string | null>(null);

  // Server-backed settings
  const [prefs, setPrefs] = useState<UserPreferences | null>(null);
  const [notif, setNotif] = useState<NotificationPreference | null>(null);
  const [closure, setClosure] = useState<AccountClosureCheck | null>(null);
  const [loadError, setLoadError] = useState("");
  const [savingPrefs, setSavingPrefs] = useState(false);

  // Modals
  const [modal, setModal] = useState<null | "email" | "password" | "close">(null);
  const [busy, setBusy] = useState(false);
  const [modalError, setModalError] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [emailPassword, setEmailPassword] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [closePassword, setClosePassword] = useState("");
  const [closeReason, setCloseReason] = useState("");

  useEffect(() => {
    if (user) {
      setDisplayName(user.fullName);
      setPhone(user.phone);
    }
  }, [user]);

  const load = useCallback(async () => {
    setLoadError("");
    try {
      const [p, n, c, e] = await Promise.all([
        getUserPreferences(),
        getNotificationPreferences(USER_NOTIFICATIONS_BASE),
        getAccountClosureCheck(),
        getPendingEmailChange(),
      ]);
      setPrefs(p);
      setNotif(n);
      setClosure(c);
      setPendingEmail(e.pendingEmail);
    } catch (err) {
      setLoadError(errorMessage(err, "Could not load your settings"));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const profileDirty = useMemo(
    () => !!user && (displayName.trim() !== user.fullName || phone.trim() !== user.phone),
    [user, displayName, phone],
  );

  function openModal(m: typeof modal) {
    setModalError("");
    setNewEmail("");
    setEmailPassword("");
    setOldPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setClosePassword("");
    setCloseReason("");
    setModal(m);
  }

  async function saveProfile() {
    if (!displayName.trim()) return showToast("Display name can't be empty.", "error");
    if (phoneError) return showToast(phoneError, "error");
    setSavingProfile(true);
    try {
      await updateUserProfile(displayName.trim(), phone.trim());
      await refreshUser();
      showToast("Profile saved.");
    } catch (err) {
      showToast(errorMessage(err, "Could not save your profile"), "error");
    } finally {
      setSavingProfile(false);
    }
  }

  async function handlePhoto(file: File | undefined) {
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) return showToast("Photo must be 5 MB or smaller.", "error");
    setPhotoBusy(true);
    try {
      await uploadProfilePhoto(file);
      await refreshUser();
      showToast("Profile photo updated.");
    } catch (err) {
      showToast(errorMessage(err, "Could not upload the photo"), "error");
    } finally {
      setPhotoBusy(false);
    }
  }

  async function handleRemovePhoto() {
    setPhotoBusy(true);
    try {
      await removeProfilePhoto();
      await refreshUser();
      showToast("Profile photo removed.");
    } catch (err) {
      showToast(errorMessage(err, "Could not remove the photo"), "error");
    } finally {
      setPhotoBusy(false);
    }
  }

  async function savePrefs(changes: Partial<UserPreferences>, message = "Saved.") {
    setSavingPrefs(true);
    try {
      const saved = await updateUserPreferences(changes);
      setPrefs(saved);
      // Density and motion change at once; dates use the new format on the next page.
      applyUserPreferences(saved);
      showToast(message);
    } catch (err) {
      showToast(errorMessage(err, "Could not save"), "error");
    } finally {
      setSavingPrefs(false);
    }
  }

  async function saveNotif(next: NotificationPreference) {
    const previous = notif;
    setNotif(next);
    try {
      setNotif(await updateNotificationPreferences(USER_NOTIFICATIONS_BASE, next));
    } catch (err) {
      setNotif(previous);
      showToast(errorMessage(err, "Could not save notification settings"), "error");
    }
  }

  async function submitEmailChange(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setModalError("");
    try {
      const res = await requestEmailChange(newEmail.trim(), emailPassword);
      setPendingEmail(res.pendingEmail);
      setModal(null);
      showToast(`We sent a confirmation link to ${res.pendingEmail}.`);
    } catch (err) {
      setModalError(errorMessage(err, "Could not start the email change"));
    } finally {
      setBusy(false);
    }
  }

  async function submitPassword(e: FormEvent) {
    e.preventDefault();
    if (newPassword.length < 8) return setModalError("New password must be at least 8 characters.");
    if (newPassword !== confirmPassword) return setModalError("The new passwords don't match.");
    setBusy(true);
    setModalError("");
    try {
      await changeUserPassword(oldPassword, newPassword);
      setModal(null);
      showToast("Password changed. Other sessions have been signed out.");
    } catch (err) {
      setModalError(errorMessage(err, "Could not change your password"));
    } finally {
      setBusy(false);
    }
  }

  async function handleSignOutOthers() {
    setBusy(true);
    try {
      await signOutOtherSessions();
      showToast("Signed out of all other sessions.");
    } catch (err) {
      showToast(errorMessage(err, "Could not sign out other sessions"), "error");
    } finally {
      setBusy(false);
    }
  }

  async function submitClose(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setModalError("");
    try {
      await closeAccount(closePassword, closeReason.trim());
      router.replace("/login");
    } catch (err) {
      setModalError(errorMessage(err, "Could not close your account"));
      setBusy(false);
    }
  }

  if (!user) return <Loader label="Loading settings..." />;

  return (
    <div className="space-y-6 pb-16">
      <Toast toast={toast} />

      <header>
        <h1 className="font-heading text-2xl font-extrabold tracking-tight text-neutral-900 sm:text-3xl dark:text-white">Settings</h1>
        <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-300">Your profile, sign-in, notifications and account.</p>
      </header>

      {loadError && (
        <div className="rounded-2xl border border-rose-200 bg-rose-50 p-3 text-sm font-semibold text-rose-800 dark:border-rose-900 dark:bg-rose-950/30 dark:text-rose-300">
          {loadError}
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav className="flex gap-1 overflow-x-auto lg:flex-col" aria-label="Settings sections">
          {SECTIONS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setSection(id)}
              className={cn(
                "flex shrink-0 items-center gap-2 rounded-xl px-3 py-2 text-left text-sm font-semibold transition",
                section === id ? "bg-primary-50 text-primary-900 dark:bg-primary-950/40 dark:text-white" : "text-neutral-600 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800",
              )}
            >
              <Icon className="h-4 w-4" />
              {label}
            </button>
          ))}
        </nav>

        <div className="space-y-5">
          {section === "profile" && (
            <>
              <section className={cardCls}>
                <div className="flex items-center gap-4">
                  <div className="relative h-16 w-16 shrink-0 overflow-hidden rounded-full bg-neutral-200 dark:bg-neutral-700">
                    {user.hasAvatar ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={profilePhotoUrl(user.avatarUpdatedAt)} alt="" className="h-full w-full object-cover" />
                    ) : (
                      <span className="flex h-full w-full items-center justify-center text-xl font-bold text-neutral-600 dark:text-neutral-200">
                        {(user.fullName || user.email).charAt(0).toUpperCase()}
                      </span>
                    )}
                  </div>
                  <div className="min-w-0">
                    <p className="truncate font-bold text-neutral-900 dark:text-white">{user.fullName}</p>
                    <p className="flex items-center gap-1 text-xs text-neutral-500">
                      {identityVerified ? (
                        <>
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600" /> Identity verified
                        </>
                      ) : (
                        "Identity not verified yet"
                      )}
                    </p>
                    <div className="mt-2 flex gap-2">
                      <label className={cn(secondaryBtn, "inline-flex cursor-pointer items-center gap-1.5", photoBusy && "pointer-events-none opacity-50")}>
                        <Camera className="h-3.5 w-3.5" /> {user.hasAvatar ? "Change photo" : "Add photo"}
                        <input
                          type="file"
                          accept="image/jpeg,image/png,image/webp"
                          className="sr-only"
                          onChange={(e) => {
                            handlePhoto(e.target.files?.[0]);
                            e.target.value = "";
                          }}
                        />
                      </label>
                      {user.hasAvatar && (
                        <button type="button" disabled={photoBusy} onClick={handleRemovePhoto} className={secondaryBtn}>
                          Remove
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </section>

              <section className={cardCls}>
                <label className="block text-xs font-bold text-neutral-700 dark:text-neutral-300">
                  Display name
                  <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={255} className={inputCls} />
                </label>
                <PhoneInputWithCountry value={phone} onChange={setPhone} error={phoneError} onErrorChange={setPhoneError} />
                <div className="flex justify-end">
                  <button type="button" disabled={!profileDirty || savingProfile} onClick={saveProfile} className={primaryBtn}>
                    {savingProfile ? "Saving…" : "Save profile"}
                  </button>
                </div>
              </section>

              <section className={cardCls}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-bold text-neutral-700 dark:text-neutral-300">Sign-in email</p>
                    <p className="text-sm text-neutral-900 dark:text-white">{user.email}</p>
                    {pendingEmail && (
                      <p className="mt-1 text-xs text-amber-700">
                        Waiting for you to confirm {pendingEmail} — check that inbox for the link.
                      </p>
                    )}
                  </div>
                  <button type="button" onClick={() => openModal("email")} className={cn(secondaryBtn, "inline-flex items-center gap-1.5")}>
                    <Mail className="h-3.5 w-3.5" /> Change
                  </button>
                </div>
              </section>
            </>
          )}

          {section === "security" && (
            <>
              <section className={cardCls}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-bold text-neutral-900 dark:text-white">Password</p>
                    <p className="text-xs text-neutral-500">Changing it signs you out everywhere else.</p>
                  </div>
                  <button type="button" onClick={() => openModal("password")} className={secondaryBtn}>
                    Change password
                  </button>
                </div>
              </section>
              <section className={cardCls}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-bold text-neutral-900 dark:text-white">Other sessions</p>
                    <p className="text-xs text-neutral-500">
                      Lost a device or signed in somewhere public? Sign out of every session except this one.
                    </p>
                  </div>
                  <button type="button" disabled={busy} onClick={handleSignOutOthers} className={cn(secondaryBtn, "inline-flex items-center gap-1.5")}>
                    <LogOut className="h-3.5 w-3.5" /> Sign out others
                  </button>
                </div>
              </section>
            </>
          )}

          {section === "notifications" && (
            <section className={cardCls}>
              {!notif || !prefs ? (
                <Loader label="Loading…" />
              ) : (
                <>
                  <h2 className="font-heading text-base font-bold text-neutral-900 dark:text-white">Notifications</h2>
                  <p className="text-xs text-neutral-500">These choices apply to notifications in the app and to emails.</p>
                  <ul className="divide-y divide-neutral-100 dark:divide-neutral-800">
                    {OPTABLE.map((c) => {
                      const on = !notif.optedOutCategories.includes(c.key);
                      return (
                        <li key={c.key} className="flex items-center justify-between gap-4 py-3">
                          <div>
                            <p className="text-sm font-semibold text-neutral-900 dark:text-white">{c.title}</p>
                            <p className="text-xs text-neutral-500">{c.description}</p>
                          </div>
                          <Toggle
                            label={c.title}
                            checked={on}
                            onChange={(v) =>
                              saveNotif({
                                ...notif,
                                optedOutCategories: v
                                  ? notif.optedOutCategories.filter((k) => k !== c.key)
                                  : [...notif.optedOutCategories, c.key],
                              })
                            }
                          />
                        </li>
                      );
                    })}
                    {ALWAYS_ON.map((c) => (
                      <li key={c.title} className="flex items-center justify-between gap-4 py-3">
                        <div>
                          <p className="text-sm font-semibold text-neutral-900 dark:text-white">{c.title}</p>
                          <p className="text-xs text-neutral-500">{c.description} Always on.</p>
                        </div>
                        <Toggle label={c.title} checked disabled onChange={() => undefined} />
                      </li>
                    ))}
                  </ul>

                  <div className="space-y-3 border-t border-neutral-100 pt-4 dark:border-neutral-800">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <p className="text-sm font-semibold text-neutral-900 dark:text-white">Quiet hours ({prefs.timeZone})</p>
                        <p className="text-xs text-neutral-500">Hold routine notifications during these hours. Urgent ones still arrive.</p>
                      </div>
                      <Toggle label="Quiet hours" checked={notif.quietHoursEnabled} onChange={(v) => saveNotif({ ...notif, quietHoursEnabled: v })} />
                    </div>
                    {notif.quietHoursEnabled && (
                      <div className="grid grid-cols-2 gap-3">
                        <label className="text-xs font-bold">
                          From
                          <input
                            type="time"
                            value={minutesToTime(notif.quietHoursStartMinute)}
                            onChange={(e) => saveNotif({ ...notif, quietHoursStartMinute: timeToMinutes(e.target.value) })}
                            className={inputCls}
                          />
                        </label>
                        <label className="text-xs font-bold">
                          Until
                          <input
                            type="time"
                            value={minutesToTime(notif.quietHoursEndMinute)}
                            onChange={(e) => saveNotif({ ...notif, quietHoursEndMinute: timeToMinutes(e.target.value) })}
                            className={inputCls}
                          />
                        </label>
                      </div>
                    )}
                  </div>

                </>
              )}
            </section>
          )}

          {section === "preferences" && (
            <section className={cardCls}>
              {!prefs ? (
                <Loader label="Loading…" />
              ) : (
                <div className="grid gap-4 sm:grid-cols-2">
                  {(
                    [
                      ["Language", "language", LANGUAGES],
                      ["Region & formats", "region", REGIONS],
                    ] as const
                  ).map(([label, key, options]) => (
                    <label key={key} className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                      {label}
                      <select
                        value={prefs[key]}
                        disabled={savingPrefs}
                        onChange={(e) => savePrefs({ [key]: e.target.value } as Partial<UserPreferences>)}
                        className={selectCls}
                      >
                        {options.map(([value, text]) => (
                          <option key={value} value={value}>{text}</option>
                        ))}
                      </select>
                    </label>
                  ))}
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Time zone
                    <select value={prefs.timeZone} disabled={savingPrefs} onChange={(e) => savePrefs({ timeZone: e.target.value })} className={selectCls}>
                      {Array.from(new Set([prefs.timeZone, ...TIME_ZONES])).map((tz) => (
                        <option key={tz} value={tz}>{tz}</option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Time format
                    <select
                      value={prefs.timeFormat}
                      disabled={savingPrefs}
                      onChange={(e) => savePrefs({ timeFormat: e.target.value as UserPreferences["timeFormat"] })}
                      className={selectCls}
                    >
                      <option value="24h">24-hour</option>
                      <option value="12h">12-hour</option>
                    </select>
                  </label>
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Density
                    <select
                      value={prefs.density}
                      disabled={savingPrefs}
                      onChange={(e) => savePrefs({ density: e.target.value as UserPreferences["density"] })}
                      className={selectCls}
                    >
                      <option value="comfortable">Comfortable</option>
                      <option value="compact">Compact</option>
                    </select>
                  </label>
                  <label className="text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Motion
                    <select
                      value={prefs.motion}
                      disabled={savingPrefs}
                      onChange={(e) => savePrefs({ motion: e.target.value as UserPreferences["motion"] })}
                      className={selectCls}
                    >
                      <option value="system">Follow my device</option>
                      <option value="reduce">Reduce motion</option>
                    </select>
                  </label>
                </div>
              )}
            </section>
          )}

          {section === "privacy" && (
            <>
              <section className={cardCls}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="font-bold text-neutral-900 dark:text-white">Download your data</p>
                    <p className="text-xs text-neutral-500">
                      Your profile, applications, tenancies, rent records, listings, support cases and notifications as a JSON file.
                    </p>
                  </div>
                  <a href={dataExportUrl()} className={cn(secondaryBtn, "inline-flex items-center gap-1.5")}>
                    <Download className="h-3.5 w-3.5" /> Download
                  </a>
                </div>
              </section>
              {prefs && (
                <section className={cardCls}>
                  {(
                    [
                      ["personalizedRecommendations", "Personalised recommendations", "In Find a Room, show rooms in cities you've applied to or searched first when sorting by Best match."],
                    ] as const
                  ).map(([key, title, description]) => (
                    <div key={key} className="flex items-center justify-between gap-4">
                      <div>
                        <p className="text-sm font-semibold text-neutral-900 dark:text-white">{title}</p>
                        <p className="text-xs text-neutral-500">{description}</p>
                      </div>
                      <Toggle label={title} checked={prefs[key]} disabled={savingPrefs} onChange={(v) => savePrefs({ [key]: v } as Partial<UserPreferences>)} />
                    </div>
                  ))}
                  <p className="text-xs text-neutral-500">
                    Agreements, rent records and dispute files are kept for their legal retention periods even if you close your account.
                  </p>
                </section>
              )}
            </>
          )}

          {section === "account" && (
            <>
              {prefs && (
                <section className={cardCls}>
                  <label className="block text-xs font-bold text-neutral-700 dark:text-neutral-300">
                    Open this workspace after sign-in
                    <select
                      value={prefs.defaultWorkspace}
                      disabled={savingPrefs}
                      onChange={(e) => savePrefs({ defaultWorkspace: e.target.value as UserPreferences["defaultWorkspace"] })}
                      className={selectCls}
                    >
                      <option value="renter">Renter</option>
                      <option value="host">Host</option>
                    </select>
                  </label>
                </section>
              )}
              <section className={cn(cardCls, "border-rose-200 dark:border-rose-900")}>
                <div className="flex items-center gap-2 text-rose-800 dark:text-rose-300">
                  <AlertTriangle className="h-4 w-4" />
                  <h2 className="font-heading text-base font-bold">Close account</h2>
                </div>
                {!closure ? (
                  <Loader label="Checking…" />
                ) : closure.canClose ? (
                  <p className="text-xs text-neutral-600 dark:text-neutral-300">
                    Nothing is blocking closure. You&apos;ll be signed out and won&apos;t be able to sign in again.
                  </p>
                ) : (
                  <>
                    <p className="text-xs text-neutral-600 dark:text-neutral-300">Resolve these first:</p>
                    <ul className="space-y-1 text-xs">
                      {closure.blockers.map((b) => (
                        <li key={b.code} className="flex justify-between rounded-lg border border-neutral-200 p-2 dark:border-neutral-800">
                          <span>{b.label}</span>
                          <strong>{b.count}</strong>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                <div className="flex justify-end">
                  <button
                    type="button"
                    disabled={!closure?.canClose}
                    onClick={() => openModal("close")}
                    className="inline-flex items-center gap-1.5 rounded-xl bg-rose-700 px-4 py-2 text-xs font-bold text-white disabled:opacity-50"
                  >
                    <Trash2 className="h-3.5 w-3.5" /> Close account
                  </button>
                </div>
              </section>
            </>
          )}
        </div>
      </div>

      {modal === "email" && (
        <Modal open onClose={() => setModal(null)} title="Change sign-in email">
          <form onSubmit={submitEmailChange} className="space-y-4 text-xs">
            <p className="text-neutral-600 dark:text-neutral-300">
              We&apos;ll email a confirmation link to the new address. Your email stays {user.email} until you open it.
            </p>
            <label className="block font-bold">
              New email
              <input type="email" required value={newEmail} onChange={(e) => setNewEmail(e.target.value)} className={inputCls} />
            </label>
            <label className="block font-bold">
              Current password
              <input type="password" required value={emailPassword} onChange={(e) => setEmailPassword(e.target.value)} autoComplete="current-password" className={inputCls} />
            </label>
            {modalError && <p className="font-semibold text-rose-600">{modalError}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setModal(null)} className={secondaryBtn}>Cancel</button>
              <button type="submit" disabled={busy} className={primaryBtn}>Send confirmation link</button>
            </div>
          </form>
        </Modal>
      )}

      {modal === "password" && (
        <Modal open onClose={() => setModal(null)} title="Change password">
          <form onSubmit={submitPassword} className="space-y-4 text-xs">
            <label className="block font-bold">
              Current password
              <input type="password" required value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} autoComplete="current-password" className={inputCls} />
            </label>
            <label className="block font-bold">
              New password (at least 8 characters)
              <input type="password" required minLength={8} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} autoComplete="new-password" className={inputCls} />
            </label>
            <label className="block font-bold">
              Confirm new password
              <input type="password" required value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} autoComplete="new-password" className={inputCls} />
            </label>
            {modalError && <p className="font-semibold text-rose-600">{modalError}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setModal(null)} className={secondaryBtn}>Cancel</button>
              <button type="submit" disabled={busy} className={primaryBtn}>Change password</button>
            </div>
          </form>
        </Modal>
      )}

      {modal === "close" && (
        <Modal open onClose={() => setModal(null)} title="Close your account">
          <form onSubmit={submitClose} className="space-y-4 text-xs">
            <p className="text-neutral-600 dark:text-neutral-300">
              This can&apos;t be undone from here. Records we must keep by law stay on file; everything else stops.
            </p>
            <label className="block font-bold">
              Why are you leaving? (optional)
              <input value={closeReason} maxLength={500} onChange={(e) => setCloseReason(e.target.value)} className={inputCls} />
            </label>
            <label className="block font-bold">
              Password
              <input type="password" required value={closePassword} onChange={(e) => setClosePassword(e.target.value)} autoComplete="current-password" className={inputCls} />
            </label>
            {modalError && <p className="font-semibold text-rose-600">{modalError}</p>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setModal(null)} className={secondaryBtn}>Cancel</button>
              <button type="submit" disabled={busy} className="rounded-xl bg-rose-700 px-4 py-2 text-xs font-bold text-white disabled:opacity-50">
                Close account
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
