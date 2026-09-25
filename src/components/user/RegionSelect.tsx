"use client";

import { OpenJurisdiction } from "@/lib/types";
import { Field, inputClass } from "@/components/user/ui";

/**
 * Region picker for a property. Only regions an admin has opened (active
 * market release + current market policy pack) are offered, so the property
 * always resolves to a real rulebook for deposits, fees, notice periods and
 * agreements. `currentCode` keeps an existing property's region selectable
 * even if that market has since closed.
 */
export function RegionSelect({
  regions,
  value,
  onChange,
  currentCode,
  locked = false,
}: {
  regions: OpenJurisdiction[];
  value: string;
  onChange: (code: string) => void;
  currentCode?: string;
  locked?: boolean;
}) {
  const options = regions.map((r) => r.code);
  if (currentCode && !options.includes(currentCode)) options.unshift(currentCode);
  const selected = regions.find((r) => r.code === value);

  let hint = "The region's rules apply to deposits, fees, notice periods and agreements for this property.";
  if (locked) {
    hint = "Locked — this property already has a live listing or a tenancy under this region's rules.";
  } else if (options.length === 0) {
    hint = "Zoiko Rooms isn't open in any region yet. Contact support to list a property.";
  } else if (selected && !selected.agreementsSupported) {
    hint = "Rental agreements in this region are prepared manually by the Zoiko team for now.";
  }

  return (
    <Field label="Region" hint={hint}>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={locked || options.length === 0}
        className={`${inputClass} disabled:cursor-not-allowed disabled:opacity-60`}
      >
        <option value="" disabled>
          Select a region
        </option>
        {options.map((code) => (
          <option key={code} value={code}>
            {code}
          </option>
        ))}
      </select>
    </Field>
  );
}
