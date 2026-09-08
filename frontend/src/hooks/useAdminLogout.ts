"use client";

import { useRouter } from "next/navigation";
import { logout } from "@/lib/auth";

/** Shared by Sidebar and Topbar so "log out and redirect" lives in one place
 *  instead of being copy-pasted in both. */
export function useAdminLogout() {
  const router = useRouter();

  return async function handleLogout() {
    await logout();
    router.push("/login");
    router.refresh();
  };
}
