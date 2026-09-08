"use client";

import { useEffect, useState } from "react";
import { getTheme, THEME_UPDATED_EVENT, type Theme } from "@/lib/theme";

export function useTheme(): Theme {
  const [theme, setThemeState] = useState<Theme>("light");

  useEffect(() => {
    setThemeState(getTheme());
    const onUpdate = () => setThemeState(getTheme());
    window.addEventListener(THEME_UPDATED_EVENT, onUpdate);
    window.addEventListener("storage", onUpdate);
    return () => {
      window.removeEventListener(THEME_UPDATED_EVENT, onUpdate);
      window.removeEventListener("storage", onUpdate);
    };
  }, []);

  return theme;
}
