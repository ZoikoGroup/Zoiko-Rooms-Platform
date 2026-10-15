import { UserSettingsManager } from "@/components/user/UserSettingsManager";

export const dynamic = "force-dynamic";

export default function SettingsPage() {
  return <UserSettingsManager defaultSection="profile" />;
}
