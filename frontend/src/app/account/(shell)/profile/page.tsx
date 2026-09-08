import { ProfileManager } from "@/components/user/ProfileManager";

export default function ProfilePage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Profile</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Manage your personal details and your password.
        </p>
      </div>
      <ProfileManager />
    </div>
  );
}
