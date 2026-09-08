"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, Image as ImageIcon, Lock, User, Bell } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Switch } from "@/components/ui/Switch";
import { Logo } from "@/components/ui/Logo";
import { getCurrentAdmin } from "@/lib/auth";
import { apiClientFetch, ApiError } from "@/lib/api-client";
import { LOGO_UPDATED_EVENT } from "@/lib/branding";
import { cn } from "@/lib/utils";

interface BrandingResponse {
  logoUrl: string;
}

interface NotificationsResponse {
  newBooking: boolean;
  payments: boolean;
  reviews: boolean;
  marketing: boolean;
}

const tabs = [
  { key: "profile", label: "Profile", icon: User },
  { key: "branding", label: "Branding", icon: ImageIcon },
  { key: "notifications", label: "Notifications", icon: Bell },
  { key: "security", label: "Security", icon: Lock },
] as const;

export function SettingsTabs() {
  const [active, setActive] = useState<(typeof tabs)[number]["key"]>("branding");
  const [profile, setProfile] = useState({ fullName: "", email: "", phone: "" });
  const [logoInput, setLogoInput] = useState("");
  const [toast, setToast] = useState("");
  const [notifs, setNotifs] = useState({ newBooking: true, payments: true, reviews: false, marketing: false });
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");

  useEffect(() => {
    getCurrentAdmin().then((admin) => {
      if (admin) setProfile({ fullName: admin.fullName, email: admin.email, phone: admin.phone });
    });
    apiClientFetch<BrandingResponse>("/api/settings/branding").then((b) => setLogoInput(b.logoUrl));
    apiClientFetch<NotificationsResponse>("/api/settings/notifications").then(setNotifs);
  }, []);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 2200);
  }

  async function handleSaveProfile() {
    try {
      const updated = await apiClientFetch<{ fullName: string; email: string; phone: string }>("/api/settings/profile", {
        method: "PUT",
        body: JSON.stringify(profile),
      });
      setProfile(updated);
      showToast("Profile updated");
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to update profile");
    }
  }

  async function saveLogo(url: string) {
    try {
      const updated = await apiClientFetch<BrandingResponse>("/api/settings/branding", {
        method: "PUT",
        body: JSON.stringify({ logoUrl: url }),
      });
      setLogoInput(updated.logoUrl);
      window.dispatchEvent(new Event(LOGO_UPDATED_EVENT));
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to update logo");
    }
  }

  function handleSaveLogo(e: React.FormEvent) {
    e.preventDefault();
    saveLogo(logoInput.trim()).then(() => showToast("Logo updated across the site"));
  }

  async function handleSaveNotifications() {
    try {
      const updated = await apiClientFetch<NotificationsResponse>("/api/settings/notifications", {
        method: "PUT",
        body: JSON.stringify(notifs),
      });
      setNotifs(updated);
      showToast("Notification preferences saved");
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to save preferences");
    }
  }

  async function handleChangePassword() {
    if (!currentPassword || !newPassword) {
      showToast("Enter both current and new password");
      return;
    }
    try {
      await apiClientFetch("/api/auth/password", {
        method: "PUT",
        body: JSON.stringify({ currentPassword, newPassword }),
      });
      setCurrentPassword("");
      setNewPassword("");
      showToast("Password updated");
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to update password");
    }
  }

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_1fr]">
      <div className="flex gap-2 overflow-x-auto lg:flex-col lg:overflow-visible">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setActive(key)}
            className={cn(
              "flex items-center gap-2.5 whitespace-nowrap rounded-xl px-4 py-2.5 text-sm font-semibold transition-all duration-200",
              active === key
                ? "bg-primary-700 text-white shadow-md shadow-primary-900/25"
                : "bg-white text-slate-600 ring-1 ring-slate-100 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-900 dark:text-slate-300 dark:ring-white/10 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
            )}
          >
            <Icon className="h-4 w-4" /> {label}
          </button>
        ))}
      </div>

      <div className="animate-fade-in rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        {active === "profile" && (
          <div className="max-w-md space-y-4">
            <h2 className="font-heading text-lg font-bold text-primary-900 dark:text-white">Profile Details</h2>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Full Name
              </label>
              <input
                value={profile.fullName}
                onChange={(e) => setProfile((p) => ({ ...p, fullName: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Email
              </label>
              <input
                value={profile.email}
                onChange={(e) => setProfile((p) => ({ ...p, email: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Phone
              </label>
              <input
                value={profile.phone}
                onChange={(e) => setProfile((p) => ({ ...p, phone: e.target.value }))}
                placeholder="+91 98200 11223"
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <Button onClick={handleSaveProfile}>Save Changes</Button>
          </div>
        )}

        {active === "branding" && (
          <div className="max-w-md space-y-4">
            <h2 className="font-heading text-lg font-bold text-primary-900 dark:text-white">Brand Logo</h2>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Paste a hosted image URL for your logo — it updates instantly across the website and admin
              panel.
            </p>

            <div className="flex items-center gap-4 rounded-xl bg-slate-50 p-4 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10">
              <Logo src={logoInput || undefined} fetchBranding />
            </div>

            <form onSubmit={handleSaveLogo} className="space-y-3">
              <div>
                <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                  Logo Image URL
                </label>
                <input
                  value={logoInput}
                  onChange={(e) => setLogoInput(e.target.value)}
                  placeholder="https://your-cdn.com/zoiko-logo.png"
                  className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                />
              </div>
              <div className="flex gap-2">
                <Button type="submit">Save Logo</Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setLogoInput("");
                    saveLogo("").then(() => showToast("Reverted to default logo"));
                  }}
                >
                  Reset to Default
                </Button>
              </div>
            </form>
          </div>
        )}

        {active === "notifications" && (
          <div className="max-w-md space-y-4">
            <h2 className="font-heading text-lg font-bold text-primary-900 dark:text-white">Notification Preferences</h2>
            {[
              { key: "newBooking", label: "New booking alerts" },
              { key: "payments", label: "Payment confirmations" },
              { key: "reviews", label: "New review alerts" },
              { key: "marketing", label: "Marketing updates" },
            ].map((item) => (
              <div key={item.key} className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800">
                <span className="text-sm font-medium text-slate-600 dark:text-slate-300">{item.label}</span>
                <Switch
                  checked={notifs[item.key as keyof typeof notifs]}
                  onChange={(v) => setNotifs((n) => ({ ...n, [item.key]: v }))}
                />
              </div>
            ))}
            <Button onClick={handleSaveNotifications}>Save Preferences</Button>
          </div>
        )}

        {active === "security" && (
          <div className="max-w-md space-y-4">
            <h2 className="font-heading text-lg font-bold text-primary-900 dark:text-white">Change Password</h2>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Current Password
              </label>
              <input
                type="password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                New Password
              </label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <Button onClick={handleChangePassword}>Update Password</Button>
          </div>
        )}
      </div>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
