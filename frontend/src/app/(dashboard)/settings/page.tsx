import { SettingsTabs } from "@/components/admin/SettingsTabs";

export default function AdminSettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Settings</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Manage your profile, branding and preferences.</p>
      </div>
      <SettingsTabs />
    </div>
  );
}
