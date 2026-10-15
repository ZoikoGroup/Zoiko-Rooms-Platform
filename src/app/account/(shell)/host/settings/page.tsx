import { UserSettingsManager } from "@/components/user/UserSettingsManager";

export const dynamic = "force-dynamic";

export default function HostSettingsPage() {
  return <UserSettingsManager defaultSection="profile" initialRole="host" />;
}
