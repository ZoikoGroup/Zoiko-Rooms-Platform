"use client";

import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import { getTheme, THEME_UPDATED_EVENT, toggleTheme, type Theme } from "@/lib/theme";
import { cn } from "@/lib/utils";

export function ThemeToggle({ className }: { className?: string }) {
  const [theme, setThemeState] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setThemeState(getTheme());
    setMounted(true);
    const onUpdate = () => setThemeState(getTheme());
    window.addEventListener(THEME_UPDATED_EVENT, onUpdate);
    window.addEventListener("storage", onUpdate);
    return () => {
      window.removeEventListener(THEME_UPDATED_EVENT, onUpdate);
      window.removeEventListener("storage", onUpdate);
    };
  }, []);

  const isDark = theme === "dark";

  return (
    <button
      type="button"
      onClick={() => setThemeState(toggleTheme())}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      title={isDark ? "Switch to light mode" : "Switch to dark mode"}
      className={cn(
        "relative flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-full text-slate-500 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-white/10 dark:hover:text-white",
        className
      )}
    >
      <span
        className={cn(
          "absolute transition-all duration-300",
          mounted && isDark ? "-translate-y-6 opacity-0 rotate-90" : "translate-y-0 opacity-100 rotate-0"
        )}
      >
        <Sun className="h-5 w-5" />
      </span>
      <span
        className={cn(
          "absolute transition-all duration-300",
          mounted && isDark ? "translate-y-0 opacity-100 rotate-0" : "translate-y-6 opacity-0 -rotate-90"
        )}
      >
        <Moon className="h-5 w-5" />
      </span>
    </button>
  );
}
