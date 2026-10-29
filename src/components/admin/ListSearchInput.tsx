"use client";

import { Search } from "lucide-react";

/** The small "filter this list" box on admin pages, so a search result that
 *  links here with ?q= shows what it matched and can be changed or cleared. */
export function ListSearchInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <div className="relative max-w-sm">
      <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={placeholder}
        className="w-full rounded-xl border border-slate-200 bg-white py-2.5 pl-10 pr-3 text-sm text-slate-700 placeholder:text-slate-400 focus:border-primary-300 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
      />
    </div>
  );
}

/** True when every word typed appears in at least one of the given fields. */
export function matchesQuery(query: string, ...fields: (string | null | undefined)[]): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = fields.map((f) => (f ?? "").toLowerCase()).join(" ");
  return words.every((w) => haystack.includes(w));
}
