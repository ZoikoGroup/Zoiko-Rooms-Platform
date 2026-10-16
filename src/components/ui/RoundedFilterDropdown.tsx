"use client";

import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown } from "lucide-react";

export interface DropdownOption {
  value: string;
  label: string;
}

export interface RoundedFilterDropdownProps {
  label?: string;
  value: string | number;
  onChange: (val: string) => void;
  options: DropdownOption[];
  placeholder?: string;
  className?: string;
  buttonClassName?: string;
  labelClassName?: string;
  align?: "left" | "right";
  size?: "sm" | "md";
  id?: string;
}

/**
 * Custom rounded filter dropdown with a rounded trigger box
 * and a rounded popover options container matching the Figma design system.
 */
export function RoundedFilterDropdown({
  label,
  value,
  onChange,
  options,
  placeholder,
  className = "",
  buttonClassName = "",
  labelClassName = "",
  align = "left",
  size = "md",
  id,
}: RoundedFilterDropdownProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }

    if (open) {
      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const selectedOption = options.find((o) => String(o.value) === String(value));
  const displayText = selectedOption?.label ?? placeholder ?? (value !== undefined && value !== null && value !== "" ? String(value) : "");
  const heightClass = size === "sm" ? "h-10 px-3.5" : "h-11 px-4";

  return (
    <div
      ref={containerRef}
      className={`flex-1 min-w-[120px] flex flex-col gap-1 relative ${
        open ? "z-30" : "z-10"
      } ${className}`}
    >
      {label && (
        <span
          className={`text-xs font-bold text-[#0C2B4E] dark:text-slate-300 font-heading ${labelClassName}`}
        >
          {label}
        </span>
      )}
      <div className="relative">
        {/* Trigger Box: Rounder pill/stadium box */}
        <button
          type="button"
          id={id}
          onClick={() => setOpen((prev) => !prev)}
          aria-haspopup="listbox"
          aria-expanded={open}
          className={`w-full ${heightClass} pr-3.5 bg-white dark:bg-slate-800 border rounded-full text-xs text-[#0C2B4E] dark:text-slate-200 outline-none flex items-center justify-between cursor-pointer transition shadow-2xs ${
            open
              ? "border-[#0C2B4E] dark:border-primary-400 ring-2 ring-primary-500/20"
              : "border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600"
          } ${buttonClassName}`}
        >
          <span className="truncate font-medium">{displayText}</span>
          <ChevronDown
            className={`size-3.5 shrink-0 transition-transform duration-200 ${
              open ? "rotate-180 text-[#0C2B4E] dark:text-primary-400" : "text-slate-400"
            }`}
          />
        </button>

        {/* Options Popover: Rounder container with rounded option pills */}
        {open && (
          <div
            role="listbox"
            className={`absolute top-full ${
              align === "right" ? "right-0" : "left-0"
            } min-w-full w-max max-w-[280px] sm:max-w-[340px] max-h-64 overflow-y-auto mt-2 z-50 bg-white dark:bg-slate-900 border border-slate-200/90 dark:border-slate-700/80 rounded-2xl shadow-xl shadow-slate-900/10 dark:shadow-black/50 p-1.5 space-y-0.5 animate-in fade-in zoom-in-95 duration-150`}
          >
            {options.map((opt) => {
              const isSelected = String(opt.value) === String(value);
              return (
                <button
                  key={opt.value}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  onClick={() => {
                    onChange(opt.value);
                    setOpen(false);
                  }}
                  className={`w-full px-3 py-2 text-xs rounded-xl text-left transition flex items-center justify-between cursor-pointer ${
                    isSelected
                      ? "bg-slate-100 dark:bg-slate-800 text-[#0C2B4E] dark:text-white font-bold"
                      : "text-slate-700 dark:text-slate-300 hover:bg-slate-50 dark:hover:bg-slate-800/60"
                  }`}
                >
                  <span className="truncate">{opt.label}</span>
                  {isSelected && (
                    <Check className="size-3.5 text-[#0C2B4E] dark:text-primary-400 stroke-[2.5] shrink-0 ml-1.5" />
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
