"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, Phone, Search } from "lucide-react";
import {
  CountryDialCode,
  SUPPORTED_COUNTRIES,
  splitPhoneNumber,
  validatePhoneWithCountry,
} from "@/lib/phone-validation";

export interface PhoneInputWithCountryProps {
  value: string;
  onChange: (fullValue: string, isValid: boolean) => void;
  error?: string;
  onErrorChange?: (error: string) => void;
  onBlur?: () => void;
  disabled?: boolean;
  className?: string;
  label?: string;
}

export function PhoneInputWithCountry({
  value,
  onChange,
  error,
  onErrorChange,
  onBlur,
  disabled = false,
  className = "",
  label = "Phone number",
}: PhoneInputWithCountryProps) {
  // Parse initial full phone
  const initial = splitPhoneNumber(value);
  const [selectedCountryCode, setSelectedCountryCode] = useState<string>(initial.countryCode);
  const [digits, setDigits] = useState<string>(initial.digits);
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [touched, setTouched] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Sync internal state when value prop changes externally (e.g. fetched from profile)
  useEffect(() => {
    const parsed = splitPhoneNumber(value);
    setSelectedCountryCode(parsed.countryCode);
    setDigits(parsed.digits);
  }, [value]);

  // Close dropdown on outside click
  useEffect(() => {
    function handleOutsideClick(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setIsOpen(false);
        setSearch("");
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleOutsideClick);
      setTimeout(() => searchInputRef.current?.focus(), 50);
    }
    return () => document.removeEventListener("mousedown", handleOutsideClick);
  }, [isOpen]);

  const activeCountry =
    SUPPORTED_COUNTRIES.find((c) => c.code === selectedCountryCode) || SUPPORTED_COUNTRIES[0];

  // Filter countries by search text
  const filteredCountries = SUPPORTED_COUNTRIES.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.code.includes(search)
  );

  function handleCountrySelect(country: CountryDialCode) {
    setSelectedCountryCode(country.code);
    setIsOpen(false);
    setSearch("");
    const full = digits ? `${country.code} ${digits}` : "";
    const validationErr = digits ? validatePhoneWithCountry(country.code, digits) : null;
    onChange(full, validationErr === null && digits.length > 0);
    if (onErrorChange && touched) {
      onErrorChange(validationErr || "");
    }
  }

  function handleDigitsChange(e: React.ChangeEvent<HTMLInputElement>) {
    const rawDigits = e.target.value.replace(/\D/g, "");
    setDigits(rawDigits);
    const full = rawDigits ? `${selectedCountryCode} ${rawDigits}` : "";
    const validationErr = validatePhoneWithCountry(selectedCountryCode, rawDigits);
    onChange(full, validationErr === null);
    if (onErrorChange && touched) {
      onErrorChange(validationErr || "");
    }
  }

  function handleInputBlur() {
    setTouched(true);
    const validationErr = validatePhoneWithCountry(selectedCountryCode, digits);
    if (onErrorChange) {
      onErrorChange(validationErr || "");
    }
    if (onBlur) {
      onBlur();
    }
  }

  const hasError = Boolean(error);
  const targetDigitsText =
    activeCountry.minDigits === activeCountry.maxDigits
      ? `${activeCountry.minDigits} digits`
      : `${activeCountry.minDigits}-${activeCountry.maxDigits} digits`;

  return (
    <div className={`space-y-1.5 ${className}`} ref={containerRef}>
      {label && (
        <div className="flex items-center justify-between">
          <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
            {label}
          </label>
          <span className="text-[11px] text-slate-400 font-normal">
            {digits.length > 0 && (
              <span
                className={
                  digits.length === activeCountry.minDigits ||
                  (digits.length >= activeCountry.minDigits &&
                    digits.length <= activeCountry.maxDigits)
                    ? "text-emerald-600 dark:text-emerald-400 font-semibold"
                    : "text-slate-400"
                }
              >
                {digits.length} / {targetDigitsText}
              </span>
            )}
          </span>
        </div>
      )}

      {/* Input Group: Country Selector on left, Digits on right */}
      <div
        className={`relative flex items-center rounded-xl bg-slate-50 ring-1 transition-all duration-200 dark:bg-slate-800 ${
          hasError
            ? "ring-red-400 focus-within:ring-2 focus-within:ring-red-500 bg-red-50/10"
            : "ring-slate-200 hover:ring-primary-300 focus-within:bg-white focus-within:ring-2 focus-within:ring-primary-400 dark:ring-slate-700 dark:focus-within:bg-slate-800"
        } ${disabled ? "opacity-60 cursor-not-allowed" : ""}`}
      >
        {/* Country Code Trigger Button */}
        <button
          type="button"
          disabled={disabled}
          onClick={() => setIsOpen((prev) => !prev)}
          className="flex items-center gap-1.5 h-11 px-3 border-r border-slate-200 dark:border-slate-700/80 bg-slate-100/60 dark:bg-slate-800 hover:bg-slate-200/50 dark:hover:bg-slate-700/60 rounded-l-xl text-xs font-semibold text-slate-800 dark:text-slate-200 transition cursor-pointer shrink-0"
          aria-haspopup="listbox"
          aria-expanded={isOpen}
        >
          <span className="text-base leading-none">{activeCountry.flag}</span>
          <span className="font-bold tracking-tight text-slate-700 dark:text-slate-200">
            {activeCountry.code}
          </span>
          <ChevronDown
            className={`h-3 w-3 text-slate-400 transition-transform duration-200 ${
              isOpen ? "rotate-180" : ""
            }`}
          />
        </button>

        {/* Digits Input */}
        <div className="relative flex-1 flex items-center">
          <Phone className="pointer-events-none absolute left-3.5 h-4 w-4 text-slate-400 shrink-0" />
          <input
            type="tel"
            disabled={disabled}
            value={digits}
            onChange={handleDigitsChange}
            onBlur={handleInputBlur}
            placeholder={`e.g. ${activeCountry.example}`}
            autoComplete="tel-national"
            className="w-full h-11 pl-10 pr-4 bg-transparent text-sm text-slate-800 dark:text-slate-100 outline-none placeholder:text-slate-400 font-medium tracking-wide"
          />
        </div>

        {/* Country Selector Dropdown Popover */}
        {isOpen && (
          <div className="absolute left-0 top-full mt-1.5 w-72 max-h-64 bg-white dark:bg-slate-900 border border-slate-200 dark:border-slate-800 rounded-2xl shadow-xl z-50 flex flex-col overflow-hidden animate-fade-in">
            {/* Search Input inside popover */}
            <div className="p-2 border-b border-slate-100 dark:border-slate-800">
              <div className="relative">
                <Search className="h-3.5 w-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
                <input
                  ref={searchInputRef}
                  type="text"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search country or code..."
                  className="w-full pl-8 pr-3 py-1.5 text-xs bg-slate-50 dark:bg-slate-800 rounded-lg outline-none border border-slate-200 dark:border-slate-700 text-slate-800 dark:text-slate-200"
                />
              </div>
            </div>

            {/* Country List */}
            <div className="overflow-y-auto p-1 flex-1 space-y-0.5">
              {filteredCountries.map((c) => {
                const isSelected = c.code === selectedCountryCode;
                return (
                  <button
                    key={c.code + c.name}
                    type="button"
                    onClick={() => handleCountrySelect(c)}
                    className={`w-full flex items-center justify-between px-3 py-2 rounded-xl text-xs transition cursor-pointer text-left ${
                      isSelected
                        ? "bg-primary-50 dark:bg-primary-950/40 text-primary-900 dark:text-primary-200 font-bold"
                        : "hover:bg-slate-50 dark:hover:bg-slate-800 text-slate-700 dark:text-slate-300"
                    }`}
                  >
                    <div className="flex items-center gap-2.5 truncate">
                      <span className="text-base">{c.flag}</span>
                      <span className="truncate">{c.name}</span>
                    </div>
                    <span className="font-semibold text-slate-400 shrink-0 ml-2">
                      {c.code}
                    </span>
                  </button>
                );
              })}
              {filteredCountries.length === 0 && (
                <div className="py-4 text-center text-xs text-slate-400">
                  No matching countries
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Error Message */}
      {hasError && (
        <p className="mt-1 text-xs font-medium text-red-500 animate-fade-in">{error}</p>
      )}
    </div>
  );
}
