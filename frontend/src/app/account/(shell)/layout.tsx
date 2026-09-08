"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { UserGuard } from "@/components/user/UserGuard";
import { UserSidebar } from "@/components/user/UserSidebar";
import { UserTopbar } from "@/components/user/UserTopbar";
import { UserChatPanel } from "@/components/user/chat/UserChatPanel";
import { UserChatLauncherFab } from "@/components/user/chat/UserChatLauncherFab";

/**
 * Shell for the signed-in USER area. `/account/login` and `/account/register` sit
 * outside this route group so they render without the guard or the chrome.
 */
export default function AccountLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [hasUnread, setHasUnread] = useState(false);
  const prevChatOpenRef = useRef(chatOpen);
  const pathname = usePathname();

  useEffect(() => {
    if (prevChatOpenRef.current && !chatOpen) {
      const timer = setTimeout(() => setHasUnread(true), 2000);
      return () => clearTimeout(timer);
    }
    if (chatOpen) {
      setHasUnread(false);
    }
    prevChatOpenRef.current = chatOpen;
  }, [chatOpen]);

  const handleUnread = useCallback(() => setHasUnread(true), []);

  return (
    <UserGuard>
      <div className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
        <UserSidebar
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((c) => !c)}
          mobileOpen={mobileOpen}
          onCloseMobile={() => setMobileOpen(false)}
          onOpenChat={() => setChatOpen(true)}
        />

        <div className="flex min-h-screen flex-1 flex-col lg:min-w-0">
          <UserTopbar onOpenMobileSidebar={() => setMobileOpen(true)} />
          <main key={pathname} className="animate-fade-up flex-1 p-4 sm:p-6 lg:p-8">
            {children}
          </main>
        </div>
      </div>

      <UserChatPanel open={chatOpen} onClose={() => setChatOpen(false)} onUnread={handleUnread} />
      <UserChatLauncherFab open={chatOpen} onToggle={() => setChatOpen((o) => !o)} hasUnread={hasUnread} />
    </UserGuard>
  );
}
