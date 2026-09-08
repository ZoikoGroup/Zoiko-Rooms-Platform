"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiClientFetch } from "@/lib/api-client";
import { LOGO_UPDATED_EVENT } from "@/lib/branding";

const DEFAULT_LOGO = "/logo.webp";

interface LogoProps {
  src?: string;
  href?: string;
  variant?: "dark" | "light";
  className?: string;
  imgClassName?: string;
  // Branding is per-admin-user (AdminSettings.admin_user_id), so fetching it only
  // makes sense where an admin session actually exists. Every other usage (public
  // login/register pages, the USER account area) has no admin to resolve branding
  // for, so it defaults to skipping the fetch entirely rather than firing a
  // guaranteed 401 and falling back anyway.
  fetchBranding?: boolean;
}

export function Logo({ src, href = "/", className, imgClassName, fetchBranding = false }: LogoProps) {
  const [storedLogo, setStoredLogo] = useState("");

  useEffect(() => {
    if (src || !fetchBranding) return;

    function fetchLogo() {
      apiClientFetch<{ logoUrl: string }>("/api/settings/branding")
        .then((b) => setStoredLogo(b.logoUrl))
        .catch(() => setStoredLogo(""));
    }

    fetchLogo();
    window.addEventListener(LOGO_UPDATED_EVENT, fetchLogo);
    return () => window.removeEventListener(LOGO_UPDATED_EVENT, fetchLogo);
  }, [src, fetchBranding]);

  const resolvedSrc = src || storedLogo || DEFAULT_LOGO;

  return (
    <Link href={href} className={`inline-flex items-center ${className ?? ""}`}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={resolvedSrc} alt="Zoiko Rooms" className={imgClassName ?? "h-9 w-auto object-contain"} />
    </Link>
  );
}
