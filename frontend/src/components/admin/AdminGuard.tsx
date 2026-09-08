"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { getCurrentAdmin } from "@/lib/auth";
import { Loader } from "@/components/ui/Loader";

export function AdminGuard({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    getCurrentAdmin().then((admin) => {
      if (admin) {
        setChecked(true);
      } else {
        router.replace("/login");
      }
    });
  }, [router]);

  if (!checked) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950">
        <Loader label="Verifying session" />
      </div>
    );
  }

  return <>{children}</>;
}
