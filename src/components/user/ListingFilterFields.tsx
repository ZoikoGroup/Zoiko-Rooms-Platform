"use client";

import type { ReactNode } from "react";
import { Check } from "lucide-react";
import { AmenitiesPicker } from "@/components/ui/AmenitiesPicker";
import {
  FILTER_ACCESSIBILITY,
  FILTER_FEATURES,
  FILTER_HANDLED_AMENITIES,
  FURNISHING_OPTIONS,
} from "@/lib/amenities";
import { deriveAccessibility, deriveFurnishedState, offersAmenity } from "@/lib/find-a-room-search";

/** Every choice here saves into the listing's amenities list, in the words the Find a Room filters use. */
function withoutFurnishing(amenities: string[]) {
  return amenities.filter((a) => !(FURNISHING_OPTIONS as readonly string[]).includes(a));
}

function Choice({
  checked,
  onChange,
  label,
  type,
  name,
}: {
  checked: boolean;
  onChange: () => void;
  label: string;
  type: "radio" | "checkbox";
  name?: string;
}) {
  return (
    <label
      className={`relative flex cursor-pointer items-center gap-2 rounded-xl px-3 py-2 text-xs font-semibold ring-1 transition-colors focus-within:ring-2 focus-within:ring-primary-500 ${
        checked
          ? "bg-primary-50 text-primary-800 ring-primary-300 dark:bg-primary-500/10 dark:text-primary-200 dark:ring-primary-500/40"
          : "bg-slate-50 text-slate-700 ring-slate-200 hover:bg-slate-100 dark:bg-slate-800/60 dark:text-slate-300 dark:ring-slate-700"
      }`}
    >
      <input type={type} name={name} checked={checked} onChange={onChange} className="absolute inset-0 h-full w-full opacity-0 cursor-pointer" />
      {checked ? <Check className="h-3.5 w-3.5 shrink-0" /> : <span className="h-3.5 w-3.5 shrink-0" />}
      <span>{label}</span>
    </label>
  );
}

function SectionTitle({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <p className="text-[11px] font-extrabold uppercase tracking-wide text-slate-500 dark:text-slate-400">
      {children}
      {required && <span className="ml-1 text-red-600">*</span>}
    </p>
  );
}

/**
 * The part of the listing form that mirrors the Find a Room filters: Furnishing, Bathroom, the features renters
 * search for (Wi-Fi, workspace, laundry, kitchen) and accessibility -- followed by any other amenities. A room that
 * doesn't answer these never matches the filter a renter chose.
 */
export function ListingFilterFields({
  amenities,
  onChange,
  bathroom,
}: {
  amenities: string[];
  onChange: (next: string[]) => void;
  /** From the room: "Private" (its own en-suite), "Shared", or null when no room is chosen yet. */
  bathroom: "Private" | "Shared" | null;
}) {
  const furnished = deriveFurnishedState(amenities);
  const accessibility = deriveAccessibility([], amenities);
  const furnishedOption =
    furnished === "yes" ? "Furnished" : furnished === "part" ? "Part Furnished" : furnished === "no" ? "Unfurnished" : null;

  function setFurnishing(option: string) {
    onChange([...withoutFurnishing(amenities), option]);
  }

  function toggleFeature(value: string, wanted: string) {
    onChange(
      offersAmenity(amenities, wanted)
        ? amenities.filter((a) => !offersAmenity([a], wanted)) // also drops older wordings, e.g. "Washing Machine"
        : [...amenities, value],
    );
  }

  function toggleAccessibility(value: string, filterName: string) {
    onChange(
      accessibility.includes(filterName)
        ? amenities.filter((a) => !deriveAccessibility([], [a]).includes(filterName))
        : [...amenities, value],
    );
  }

  // A plain <div>, not the form's <Field> (which is a <label>): this block has its own labels and inputs, and a
  // label inside a label would tick the first option whenever the text around it is clicked.
  return (
    <div className="space-y-5 rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900/40">
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Furnishing, features &amp; accessibility
        </h3>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Renters filter rooms by these, so answer each one — a room that doesn&apos;t say won&apos;t show up for that filter.
        </p>
      </div>

      <div className="space-y-2">
        <SectionTitle required>Furnishing</SectionTitle>
        <div className="flex flex-wrap gap-2">
          {FURNISHING_OPTIONS.map((option) => (
            <Choice
              key={option}
              type="radio"
              name="listing-furnishing"
              label={option}
              checked={furnishedOption === option}
              onChange={() => setFurnishing(option)}
            />
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <SectionTitle>Bathroom</SectionTitle>
        <p className="text-xs font-semibold text-slate-700 dark:text-slate-200">
          {bathroom === "Private"
            ? "Private bathroom (en-suite)"
            : bathroom === "Shared"
              ? "Shared bathroom"
              : "Set by the room you choose"}
          <span className="ml-1 font-normal text-slate-500 dark:text-slate-400">
            — taken from the room&apos;s &ldquo;private en-suite&rdquo; setting, so it can&apos;t disagree with the room.
          </span>
        </p>
      </div>

      <div className="space-y-2">
        <SectionTitle>Features renters search for</SectionTitle>
        <div className="flex flex-wrap gap-2">
          {FILTER_FEATURES.map((f) => (
            <Choice
              key={f.value}
              type="checkbox"
              label={f.label}
              checked={offersAmenity(amenities, f.wanted)}
              onChange={() => toggleFeature(f.value, f.wanted)}
            />
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <SectionTitle>Accessibility</SectionTitle>
        <div className="flex flex-wrap gap-2">
          {FILTER_ACCESSIBILITY.map((a) => (
            <Choice
              key={a.value}
              type="checkbox"
              label={a.label}
              checked={accessibility.includes(a.filterName)}
              onChange={() => toggleAccessibility(a.value, a.filterName)}
            />
          ))}
        </div>
      </div>

      <div className="space-y-2">
        <SectionTitle>Other amenities</SectionTitle>
        <AmenitiesPicker value={amenities} onChange={onChange} hide={FILTER_HANDLED_AMENITIES} />
      </div>
    </div>
  );
}

/** True when the listing says whether the room is furnished, part furnished or unfurnished. */
export function hasFurnishingAnswer(amenities: string[]): boolean {
  return deriveFurnishedState(amenities) !== "unknown";
}
