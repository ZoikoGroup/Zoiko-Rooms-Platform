"use client";

import {
  ListingAgreementDetails,
  ListingHouseRules,
  UtilityKey,
  UtilityPayer,
} from "@/lib/types";
import { Field, inputClass } from "@/components/user/ui";

/** Schedule B rows, in the order the agreement prints them. */
const UTILITIES: { key: UtilityKey; label: string }[] = [
  { key: "electricity", label: "Electricity" },
  { key: "gas_heating", label: "Gas / heating" },
  { key: "water_sewer", label: "Water / sewer" },
  { key: "internet", label: "Internet" },
  { key: "local_taxes", label: "Local / occupancy taxes" },
  { key: "building_fees", label: "Building / service fees" },
];

const PAYERS: { value: UtilityPayer | ""; label: string }[] = [
  { value: "", label: "Not specified" },
  { value: "HOST", label: "Host pays" },
  { value: "RENTER", label: "Renter pays" },
  { value: "SHARED", label: "Shared" },
  { value: "INCLUDED", label: "Included in rent" },
  { value: "NOT_APPLICABLE", label: "Not applicable" },
];

const HOUSE_RULES: { key: keyof ListingHouseRules; label: string; placeholder: string }[] = [
  { key: "guests", label: "Guests", placeholder: "e.g. Overnight guests up to 2 nights a week" },
  { key: "pets", label: "Pets", placeholder: "e.g. No pets without written consent" },
  { key: "smoking", label: "Smoking / vaping", placeholder: "e.g. No smoking or vaping indoors" },
  { key: "noise", label: "Noise / quiet hours", placeholder: "e.g. Quiet hours 10pm–7am" },
  { key: "parkingStorage", label: "Parking / storage", placeholder: "e.g. One bike space in the hallway" },
  { key: "sharedAreas", label: "Shared areas", placeholder: "e.g. Clean the kitchen after use" },
];

export const emptyAgreementDetails: ListingAgreementDetails = {
  exclusiveUseAreas: "",
  sharedUseAreas: "",
  hostServiceAddress: "",
  rentDueRule: "",
  renewalRule: "",
  utilities: {},
  houseRules: { guests: "", pets: "", smoking: "", noise: "", parkingStorage: "", sharedAreas: "" },
};

/** Normalizes whatever the API returned (older listings may have `{}`). */
export function toAgreementDetailsForm(details: Partial<ListingAgreementDetails> | undefined): ListingAgreementDetails {
  return {
    ...emptyAgreementDetails,
    ...details,
    utilities: { ...(details?.utilities ?? {}) },
    houseRules: { ...emptyAgreementDetails.houseRules, ...(details?.houseRules ?? {}) },
  };
}

/**
 * Host-supplied facts for the Residential Occupancy Agreement (Schedule A
 * areas, notice address, rent due / renewal rules; Schedule B utilities and
 * property rules). Anything left blank prints as "Not specified". Changes
 * apply to agreements generated afterwards — already-generated agreements
 * keep the details they were created with.
 */
export function ListingAgreementDetailsFields({
  value,
  onChange,
}: {
  value: ListingAgreementDetails;
  onChange: (next: ListingAgreementDetails) => void;
}) {
  const set = <K extends keyof ListingAgreementDetails>(key: K, v: ListingAgreementDetails[K]) =>
    onChange({ ...value, [key]: v });

  function setUtility(key: UtilityKey, patch: { payer?: UtilityPayer | ""; notes?: string }) {
    const current = value.utilities[key] ?? { payer: null, notes: "" };
    const next = {
      payer: (patch.payer !== undefined ? patch.payer || null : current.payer) as UtilityPayer | null,
      notes: patch.notes !== undefined ? patch.notes : current.notes,
    };
    const utilities = { ...value.utilities };
    if (!next.payer && !next.notes.trim()) delete utilities[key];
    else utilities[key] = next;
    set("utilities", utilities);
  }

  return (
    <div className="space-y-4 rounded-xl bg-slate-50 p-4 ring-1 ring-slate-100 dark:bg-slate-800/60 dark:ring-white/10">
      <div>
        <p className="text-xs font-semibold text-primary-900 dark:text-white">
          Agreement details <span className="font-normal text-slate-400">(optional, printed in the rental agreement)</span>
        </p>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          These appear in Schedules A and B of the agreement your renter signs. Anything left blank shows as
          &ldquo;Not specified&rdquo;. Changes only affect agreements created after you save.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="Exclusive-use areas">
          <input
            value={value.exclusiveUseAreas}
            onChange={(e) => set("exclusiveUseAreas", e.target.value)}
            maxLength={300}
            placeholder="e.g. Bedroom and ensuite"
            className={inputClass}
          />
        </Field>
        <Field label="Shared-use areas">
          <input
            value={value.sharedUseAreas}
            onChange={(e) => set("sharedUseAreas", e.target.value)}
            maxLength={300}
            placeholder="e.g. Kitchen, living room, garden"
            className={inputClass}
          />
        </Field>
        <Field label="Your formal notice address">
          <input
            value={value.hostServiceAddress}
            onChange={(e) => set("hostServiceAddress", e.target.value)}
            maxLength={500}
            placeholder="Where legal notices to you should be sent"
            className={inputClass}
          />
        </Field>
        <Field label="Rent due">
          <input
            value={value.rentDueRule}
            onChange={(e) => set("rentDueRule", e.target.value)}
            maxLength={200}
            placeholder="e.g. 1st of each month"
            className={inputClass}
          />
        </Field>
      </div>
      <Field label="Renewal">
        <input
          value={value.renewalRule}
          onChange={(e) => set("renewalRule", e.target.value)}
          maxLength={300}
          placeholder="e.g. Continues month to month unless either party gives notice"
          className={inputClass}
        />
      </Field>

      <div className="space-y-2">
        <p className="text-xs font-semibold text-slate-600 dark:text-slate-300">Utilities and recurring charges</p>
        {UTILITIES.map(({ key, label }) => {
          const allocation = value.utilities[key];
          return (
            <div key={key} className="grid grid-cols-1 items-center gap-2 sm:grid-cols-[10rem_11rem_1fr]">
              <span className="text-xs text-slate-600 dark:text-slate-300">{label}</span>
              <select
                aria-label={`${label}: who pays`}
                value={allocation?.payer ?? ""}
                onChange={(e) => setUtility(key, { payer: e.target.value as UtilityPayer | "" })}
                className={inputClass}
              >
                {PAYERS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              <input
                aria-label={`${label}: notes`}
                value={allocation?.notes ?? ""}
                onChange={(e) => setUtility(key, { notes: e.target.value })}
                maxLength={200}
                placeholder="Notes (optional)"
                className={inputClass}
              />
            </div>
          );
        })}
      </div>

      <div className="space-y-2">
        <p className="text-xs font-semibold text-slate-600 dark:text-slate-300">
          Property rules <span className="font-normal text-slate-400">(always subject to mandatory law)</span>
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {HOUSE_RULES.map(({ key, label, placeholder }) => (
            <Field key={key} label={label}>
              <input
                value={value.houseRules[key]}
                onChange={(e) => set("houseRules", { ...value.houseRules, [key]: e.target.value })}
                maxLength={500}
                placeholder={placeholder}
                className={inputClass}
              />
            </Field>
          ))}
        </div>
      </div>
    </div>
  );
}
