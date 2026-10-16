"use client";

import { Check, ChevronDown, Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";

export interface ComboboxOption {
  value: string;
  label: string;
  /** Secondary text, e.g. a region tag ("UK"). */
  hint?: string;
  icon?: React.ReactNode;
}

/** Lists longer than this get a search box (ZR-PAY-METHODS-GLOBAL-001 7.1). */
export const SEARCHABLE_OPTION_THRESHOLD = 8;

/**
 * WAI-ARIA 1.2 combobox + listbox: fully keyboard operable (arrows, Home/End,
 * Enter, Escape, type-to-search) and exposes the selected value, expanded
 * state and active option to assistive technology (Section 9). With more
 * than SEARCHABLE_OPTION_THRESHOLD options the trigger becomes a filterable
 * text input instead of a 50-item native dropdown.
 */
export function OptionCombobox({
  labelId,
  options,
  value,
  onChange,
  placeholder,
  invalid,
  describedBy,
  required,
}: {
  labelId: string;
  options: ComboboxOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  invalid?: boolean;
  describedBy?: string;
  required?: boolean;
}) {
  const listId = useId();
  const optionIdPrefix = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const searchable = options.length > SEARCHABLE_OPTION_THRESHOLD;
  const selected = options.find((o) => o.value === value);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter((o) => `${o.label} ${o.hint ?? ""}`.toLowerCase().includes(q));
  }, [options, query]);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  useEffect(() => {
    if (open && searchable) searchRef.current?.focus();
  }, [open, searchable]);

  useEffect(() => {
    if (!open) return;
    document.getElementById(`${optionIdPrefix}-${active}`)?.scrollIntoView({ block: "nearest" });
  }, [active, open, optionIdPrefix]);

  function openList() {
    const index = Math.max(0, options.findIndex((o) => o.value === value));
    setQuery("");
    setActive(index);
    setOpen(true);
  }

  function choose(option: ComboboxOption | undefined) {
    if (!option) return;
    onChange(option.value);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openList();
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((i) => Math.min(i + 1, filtered.length - 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
        break;
      case "Home":
        e.preventDefault();
        setActive(0);
        break;
      case "End":
        e.preventDefault();
        setActive(filtered.length - 1);
        break;
      case "Enter":
        e.preventDefault();
        choose(filtered[active]);
        break;
      case " ":
        if (!searchable) {
          e.preventDefault();
          choose(filtered[active]);
        }
        break;
      case "Escape":
        // Close just the list -- not the surrounding modal.
        e.preventDefault();
        e.stopPropagation();
        setOpen(false);
        triggerRef.current?.focus();
        break;
      case "Tab":
        setOpen(false);
        break;
    }
  }

  const activeId = open && filtered[active] ? `${optionIdPrefix}-${active}` : undefined;

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-labelledby={labelId}
        aria-controls={listId}
        aria-expanded={open}
        aria-haspopup="listbox"
        aria-activedescendant={searchable ? undefined : activeId}
        aria-invalid={invalid || undefined}
        aria-required={required || undefined}
        aria-describedby={describedBy}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={onKeyDown}
        className={cn(
          "flex w-full items-center gap-2.5 rounded-xl bg-white px-3.5 py-2.5 text-left text-sm text-slate-800 outline-none ring-1 transition-all focus-visible:ring-2 focus-visible:ring-primary-400 dark:bg-slate-800 dark:text-slate-100",
          invalid ? "ring-rose-400" : open ? "ring-2 ring-primary-400" : "ring-slate-200 dark:ring-slate-700"
        )}
      >
        {selected?.icon && <span className="shrink-0 text-primary-700 dark:text-primary-300" aria-hidden="true">{selected.icon}</span>}
        <span className={cn("min-w-0 flex-1 truncate", !selected && "text-slate-400")}>
          {selected ? selected.label : placeholder}
          {selected?.hint && <span className="ml-1.5 text-xs text-slate-400">({selected.hint})</span>}
        </span>
        <ChevronDown className={cn("h-4 w-4 shrink-0 text-slate-400 transition-transform", open && "rotate-180")} aria-hidden="true" />
      </button>

      {open && (
        <div className="absolute left-0 right-0 z-30 mt-1.5 overflow-hidden rounded-xl bg-white shadow-xl ring-1 ring-slate-200 dark:bg-slate-900 dark:ring-slate-700">
          {searchable && (
            <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2 dark:border-slate-800">
              <Search className="h-4 w-4 text-slate-400" aria-hidden="true" />
              <input
                ref={searchRef}
                type="text"
                role="combobox"
                aria-label="Search options"
                aria-controls={listId}
                aria-expanded="true"
                aria-autocomplete="list"
                aria-activedescendant={activeId}
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActive(0);
                }}
                onKeyDown={onKeyDown}
                placeholder="Search..."
                className="w-full bg-transparent text-sm text-slate-800 outline-none dark:text-slate-100"
              />
            </div>
          )}
          <ul id={listId} role="listbox" aria-labelledby={labelId} className="max-h-72 overflow-y-auto p-1.5">
            {filtered.length === 0 && <li className="px-3 py-2 text-sm text-slate-400">No matches</li>}
            {filtered.map((option, index) => {
              const isSelected = option.value === value;
              return (
                <li
                  key={option.value}
                  id={`${optionIdPrefix}-${index}`}
                  role="option"
                  aria-selected={isSelected}
                  onPointerMove={() => setActive(index)}
                  onClick={() => choose(option)}
                  className={cn(
                    "flex cursor-pointer items-center gap-2.5 rounded-lg px-3 py-2 text-sm text-slate-700 dark:text-slate-200",
                    index === active && "bg-primary-50 dark:bg-primary-500/15",
                    isSelected && "font-semibold text-primary-900 dark:text-white"
                  )}
                >
                  {option.icon && <span className="shrink-0 text-primary-700 dark:text-primary-300" aria-hidden="true">{option.icon}</span>}
                  <span className="min-w-0 flex-1">
                    {option.label}
                    {option.hint && <span className="ml-1.5 text-xs font-normal text-slate-400">({option.hint})</span>}
                  </span>
                  {isSelected && <Check className="h-4 w-4 shrink-0 text-primary-600" aria-hidden="true" />}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
