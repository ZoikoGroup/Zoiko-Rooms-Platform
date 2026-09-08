import { TeamManager } from "@/components/admin/TeamManager";
import { requireSuperAdmin } from "@/lib/api";

export default async function AdminTeamPage() {
  await requireSuperAdmin();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-heading text-2xl font-extrabold text-primary-900 dark:text-white">Team</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Approve new admin registrations, and manage roles and access for your team.
        </p>
      </div>
      <TeamManager />
    </div>
  );
}
