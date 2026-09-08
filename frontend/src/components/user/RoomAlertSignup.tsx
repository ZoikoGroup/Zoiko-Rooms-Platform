"use client";

import { useState, type FormEvent } from "react";
import { BellPlus } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { createRoomAlert } from "@/lib/user-api";
import { Card, Field, Toast, inputClass, useToast } from "@/components/user/ui";

/** Public, no-login signup for "email me when a new room matching this shows
 *  up" -- the counterpart to RentBrowser's search, for a visitor who didn't
 *  find a match today. Backend sends a confirmation email with an unsubscribe
 *  link; matching new listings against saved alerts runs on a schedule
 *  (see backend/check_alerts.py), not from this request. */
export function RoomAlertSignup() {
  const { toast, showToast } = useToast();
  const [email, setEmail] = useState("");
  const [city, setCity] = useState("");
  const [maxPrice, setMaxPrice] = useState("");
  const [roomType, setRoomType] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [saved, setSaved] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!email.trim() || !city.trim()) {
      showToast("Email and city are required.", "error");
      return;
    }
    setSubmitting(true);
    try {
      await createRoomAlert({
        email: email.trim(),
        city: city.trim(),
        maxPrice: maxPrice.trim() ? Number(maxPrice) : null,
        roomType: roomType.trim() || null,
      });
      setSaved(true);
      showToast("Alert saved — check your email to confirm.");
    } catch {
      showToast("Could not save this alert. Please try again.", "error");
    } finally {
      setSubmitting(false);
    }
  }

  if (saved) {
    return (
      <Card>
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400">
            <BellPlus className="h-5 w-5" />
          </span>
          <p className="text-sm text-slate-600 dark:text-slate-300">
            You&apos;re set — we&apos;ll email <span className="font-semibold">{email}</span> when a new room in{" "}
            <span className="font-semibold">{city}</span> matches.
          </p>
        </div>
      </Card>
    );
  }

  return (
    <Card>
      <div className="flex items-center gap-2">
        <BellPlus className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-sm font-bold text-primary-900 dark:text-white">Not seeing the right room?</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Get an email the moment a new room matching your criteria is published — no account needed.
      </p>
      <form onSubmit={handleSubmit} className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Field label="Email">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@example.com"
            className={inputClass}
            required
          />
        </Field>
        <Field label="City">
          <input value={city} onChange={(e) => setCity(e.target.value)} placeholder="e.g. Bengaluru" className={inputClass} required />
        </Field>
        <Field label="Max price (optional)">
          <input
            inputMode="numeric"
            value={maxPrice}
            onChange={(e) => setMaxPrice(e.target.value)}
            placeholder="e.g. 20000"
            className={inputClass}
          />
        </Field>
        <Field label="Room type (optional)">
          <input
            value={roomType}
            onChange={(e) => setRoomType(e.target.value)}
            placeholder="e.g. Private room"
            className={inputClass}
          />
        </Field>
        <div className="flex items-end">
          <Button type="submit" loading={submitting} fullWidth>
            Create alert
          </Button>
        </div>
      </form>
      <Toast toast={toast} />
    </Card>
  );
}
