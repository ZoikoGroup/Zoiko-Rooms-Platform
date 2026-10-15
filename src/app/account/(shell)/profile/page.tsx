import { UserSettingsManager } from "@/components/user/UserSettingsManager";

export const dynamic = "force-dynamic";

export default function ProfilePage() {
  return <UserSettingsManager defaultSection="profile" />;
}

