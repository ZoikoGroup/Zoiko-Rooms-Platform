"use client";

import { useState, type KeyboardEvent } from "react";
import { Check, Plus, X } from "lucide-react";
import { COMMON_AMENITIES, COMMON_AMENITY_VALUES } from "@/lib/amenities";

/**
 * Pick-from-a-list amenities input, backed by the same string[] the backend
 * already stores (models/listing.py has no fixed amenity vocabulary). Anything
 * a host adds via "Other" is kept and rendered as its own removable chip, so
 * nothing typed in the old free-text field is lost by switching to this.
 */
export function AmenitiesPicker({ value, onChange }: { value: string[]; onChange: (next: string[]) => void }) {
  const [customInput, setCustomInput] = useState("");

  const customAmenities = value.filter((a) => !COMMON_AMENITY_VALUES.has(a));

  function toggle(amenity: string) {
    onChange(value.includes(amenity) ? value.filter((a) => a !== amenity) : [...value, amenity]);
  }

  function addCustom() {
    const trimmed = customInput.trim();
    if (!trimmed || value.includes(trimmed)) {
      setCustomInput("");
      return;
    }
    onChange([...value, trimmed]);
    setCustomInput("");
  }

  function removeCustom(amenity: string) {
    onChange(value.filter((a) => a !== amenity));
  }

  function handleCustomKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      addCustom();
    }
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {COMMON_AMENITIES.map((amenity) => {
          const selected = value.includes(amenity.value);
          return (
            <button
              key={amenity.value}
              type="button"
              onClick={() => toggle(amenity.value)}
              aria-pressed={selected}
              className={`flex items-center gap-1.5 rounded-xl px-3 py-2 text-left text-xs font-semibold transition-all ${
                selected
                  ? "bg-primary-700 text-white shadow-sm shadow-primary-900/20"
                  : "bg-slate-50 text-slate-600 ring-1 ring-slate-200 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-300 dark:ring-slate-700"
              }`}
            >
              {selected ? <Check className="h-3.5 w-3.5 shrink-0" /> : null}
              <span className="truncate">{amenity.label}</span>
            </button>
          );
        })}
      </div>

      {customAmenities.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {customAmenities.map((amenity) => (
            <span
              key={amenity}
              className="flex items-center gap-1.5 rounded-full bg-primary-50 px-3 py-1 text-xs font-semibold text-primary-700 dark:bg-primary-500/10 dark:text-primary-300"
            >
              {amenity}
              <button type="button" onClick={() => removeCustom(amenity)} aria-label={`Remove ${amenity}`}>
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="flex gap-2">
        <input
          value={customInput}
          onChange={(e) => setCustomInput(e.target.value)}
          onKeyDown={handleCustomKeyDown}
          placeholder="Something else? Add your own amenity"
          className="w-full rounded-xl bg-slate-50 px-4 py-2 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
        />
        <button
          type="button"
          onClick={addCustom}
          className="flex shrink-0 items-center gap-1 rounded-xl bg-slate-100 px-3 py-2 text-xs font-semibold text-slate-600 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-300"
        >
          <Plus className="h-3.5 w-3.5" /> Add
        </button>
      </div>
    </div>
  );
}
