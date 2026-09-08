"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { AdminGuard } from "@/components/admin/AdminGuard";
import { ChatLauncherFab } from "@/components/admin/ChatLauncherFab";
import { AdminChatPanel } from "@/components/admin/chat/AdminChatPanel";
import { Sidebar } from "@/components/admin/Sidebar";
import { Topbar } from "@/components/admin/Topbar";

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [hasUnread, setHasUnread] = useState(false);
  const prevChatOpenRef = useRef(chatOpen);
  const pathname = usePathname();

  // Track when the chat panel closes — if the admin was mid-conversation,
  // mark the launcher as unread until they reopen the panel.
  useEffect(() => {
    if (prevChatOpenRef.current && !chatOpen) {
      // Panel just closed — assume there may be an unread response.
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
    <AdminGuard>
      <div className="flex min-h-screen bg-slate-50 dark:bg-slate-950">
        <Sidebar
          collapsed={collapsed}
          onToggleCollapse={() => setCollapsed((c) => !c)}
          mobileOpen={mobileOpen}
          onCloseMobile={() => setMobileOpen(false)}
        />

        <div className="flex min-h-screen flex-1 flex-col lg:min-w-0">
          <Topbar onOpenMobileSidebar={() => setMobileOpen(true)} onToggleChat={() => setChatOpen((o) => !o)} />
          <main key={pathname} className="animate-fade-up flex-1 p-4 sm:p-6 lg:p-8">
            {children}
          </main>
        </div>
      </div>

      <AdminChatPanel open={chatOpen} onClose={() => setChatOpen(false)} onUnread={handleUnread} />
      <ChatLauncherFab open={chatOpen} onToggle={() => setChatOpen((o) => !o)} hasUnread={hasUnread} />
    </AdminGuard>
  );
}
