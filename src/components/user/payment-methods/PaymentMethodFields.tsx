"use client";

import {
  AlertTriangle,
  Banknote,
  CalendarClock,
  ChevronDown,
  CreditCard,
  FileCheck,
  FileSignature,
  Info,
  Landmark,
  Lock,
  MoreHorizontal,
  RefreshCw,
  ScrollText,
  Send,
  Smartphone,
  Upload,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import type {
  PaymentMethodCatalog,
  PaymentMethodCategory,
  PaymentMethodCategoryOption,
  PaymentMethodContext,
  PaymentMethodFieldRules,
} from "@/lib/types";
import { ApiError } from "@/lib/api-client";
import { errorMessage, getPaymentMethodCatalog } from "@/lib/user-api";
import { cn } from "@/lib/utils";
import { inputClass } from "@/components/user/ui";
import { ComboboxOption, OptionCombobox } from "./OptionCombobox";

/* ZR-PAY-METHODS-GLOBAL-001: the shared pieces every payment-recording form
 * uses -- Payment Method -> conditional Rail/Provider -> dynamic Reference
 * -> Evidence / Provenance. Options always come from the server catalog for
 * the rental's country; the form submits canonical codes only. */

const CATEGORY_ICON: Record<PaymentMethodCategory, React.ComponentType<{ className?: string }>> = {
  BANK_TRANSFER: Landmark,
  INSTANT_BANK_PAYMENT: Zap,
  BANK_DEBIT: RefreshCw,
  STANDING_ORDER: CalendarClock,
  PAYMENT_CARD: CreditCard,
  DIGITAL_WALLET_P2P: Wallet,
  MOBILE_MONEY: Smartphone,
  REMITTANCE_SERVICE: Send,
  CASH: Banknote,
  CHEQUE_CHECK: FileSignature,
  BANK_DRAFT: FileCheck,
  MONEY_ORDER: ScrollText,
  OTHER: MoreHorizontal,
};

export function PaymentCategoryIcon({ category, className }: { category: string; className?: string }) {
  const Icon = CATEGORY_ICON[category as PaymentMethodCategory] ?? MoreHorizontal;
  return <Icon className={className ?? "h-4 w-4"} />;
}

/** Category labels for display fallback only -- records carry their own
 *  label snapshot (paymentMethodLabel), which always wins. */
const CATEGORY_LABELS: Record<string, string> = {
  BANK_TRANSFER: "Bank Transfer",
  INSTANT_BANK_PAYMENT: "Instant Bank Payment / Pay by Bank",
  BANK_DEBIT: "Direct Debit / Bank Debit",
  STANDING_ORDER: "Standing Order / Scheduled Bank Transfer",
  PAYMENT_CARD: "Payment Card",
  DIGITAL_WALLET_P2P: "Digital Wallet / P2P Payment App",
  MOBILE_MONEY: "Mobile Money",
  REMITTANCE_SERVICE: "Remittance / Money Transfer Service",
  CASH: "Cash",
  CHEQUE_CHECK: "Cheque / Check",
  BANK_DRAFT: "Bank Draft / Cashier's Check",
  MONEY_ORDER: "Money Order",
  OTHER: "Other",
  // Values recorded before the global taxonomy.
  UPI: "Instant Bank Payment / Pay by Bank · UPI",
  CARD: "Payment Card",
};

/** The currency's narrow symbol ("£", "$", "₹"), or the code itself. */
export function currencySymbol(currency: string): string {
  try {
    const parts = new Intl.NumberFormat(undefined, { style: "currency", currency, currencyDisplay: "narrowSymbol" }).formatToParts(0);
    return parts.find((p) => p.type === "currency")?.value ?? currency;
  } catch {
    return currency;
  }
}

/** How to show the method on any stored payment record or return. */
export function paymentMethodDisplay(item: {
  paymentMethodLabel?: string;
  paymentMethodCategory: string;
  paymentMethodSubtype?: string;
}): string {
  if (item.paymentMethodLabel) return item.paymentMethodLabel;
  const base = CATEGORY_LABELS[item.paymentMethodCategory] ?? item.paymentMethodCategory.replace(/_/g, " ").toLowerCase();
  return item.paymentMethodSubtype ? `${base} · ${item.paymentMethodSubtype.replace(/_/g, " ")}` : base;
}

/** Section 8: list views show only the end of an external reference. */
export function maskReference(reference: string): string {
  const ref = reference.trim();
  return ref.length <= 4 ? ref : `•••• ${ref.slice(-4)}`;
}

export interface PaymentMethodValue {
  category: PaymentMethodCategory | "";
  subtype: string;
  description: string;
  reference: string;
}

export const EMPTY_PAYMENT_METHOD: PaymentMethodValue = { category: "", subtype: "", description: "", reference: "" };

export type PaymentMethodErrors = Partial<Record<"category" | "subtype" | "description" | "reference", string>>;

export function usePaymentMethodCatalog(country: string | undefined, context: PaymentMethodContext, enabled = true) {
  const [catalog, setCatalog] = useState<PaymentMethodCatalog | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setError("");
    getPaymentMethodCatalog(country ?? "", context)
      .then((c) => !cancelled && setCatalog(c))
      .catch((err) => !cancelled && setError(errorMessage(err, "Could not load payment methods.")));
    return () => {
      cancelled = true;
    };
  }, [country, context, enabled]);
  return { catalog, error };
}

/** Section 5: the subtype's own reference wording wins over its category's. */
export function resolveFieldRules(
  catalog: PaymentMethodCatalog | null,
  value: PaymentMethodValue
): { category?: PaymentMethodCategoryOption; rules?: PaymentMethodFieldRules } {
  const category = catalog?.categories.find((c) => c.code === value.category);
  if (!category) return {};
  const subtype = category.subtypes.find((s) => s.code === value.subtype);
  return { category, rules: subtype ?? category };
}

/** Client-side check mirroring services/payment_method_catalog.py's
 *  resolve_selection -- the server stays the final authority. */
export function validatePaymentMethod(catalog: PaymentMethodCatalog | null, value: PaymentMethodValue): PaymentMethodErrors {
  const errors: PaymentMethodErrors = {};
  const { category, rules } = resolveFieldRules(catalog, value);
  if (!category) {
    errors.category = "Choose the payment method you used.";
    return errors;
  }
  if (category.subtypeRequired && !value.subtype) errors.subtype = `Choose the ${category.subtypeLabel.toLowerCase()}.`;
  if (rules?.requiresDescription && !value.description.trim()) errors.description = "Describe how the payment was made.";
  if (rules?.referencePolicy === "REQUIRED" && !value.reference.trim())
    errors.reference = `Enter the ${rules.referenceLabel.toLowerCase()}.`;
  if (/(?:\d[ -]?){12,18}\d/.test(value.reference) && value.category === "PAYMENT_CARD")
    errors.reference = "Never enter a full card number -- use the merchant reference or the last 4 digits.";
  return errors;
}

function FieldLabel({ id, htmlFor, text, required, optional }: { id?: string; htmlFor?: string; text: string; required?: boolean; optional?: boolean }) {
  return (
    <label id={id} htmlFor={htmlFor} className="mb-1.5 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
      {text}
      {required && <span className="text-rose-500" aria-hidden="true"> *</span>}
      {optional && <span className="ml-1 font-normal normal-case tracking-normal text-slate-400">(Optional)</span>}
    </label>
  );
}

function FieldError({ id, message }: { id: string; message?: string }) {
  if (!message) return null;
  return (
    <p id={id} className="mt-1 text-xs font-medium text-rose-600 dark:text-rose-400">
      {message}
    </p>
  );
}

/**
 * Payment method -> conditional rail/provider -> (Other) description ->
 * reference with method-specific label and helper text. Fields appear in
 * that DOM order (Section 9); the second control appears only when the
 * chosen category has configured rails (criterion 6).
 */
export function PaymentMethodFields({
  catalog,
  catalogError,
  value,
  onChange,
  errors,
  categoryLabel = "Payment method",
}: {
  catalog: PaymentMethodCatalog | null;
  catalogError?: string;
  value: PaymentMethodValue;
  onChange: (value: PaymentMethodValue) => void;
  errors: PaymentMethodErrors;
  categoryLabel?: string;
}) {
  const ids = useId();
  const { category, rules } = resolveFieldRules(catalog, value);
  const [notice, setNotice] = useState("");

  // Section 9: a selection that stops being valid (e.g. the catalog for a
  // different country loaded) is cleared explicitly, with a reason.
  useEffect(() => {
    if (!catalog || !value.category) return;
    if (!catalog.categories.some((c) => c.code === value.category)) {
      onChange(EMPTY_PAYMENT_METHOD);
      setNotice("The payment method you picked isn't available for this rental, so it was cleared. Please choose again.");
    }
  }, [catalog, value.category, onChange]);

  function selectCategory(code: string) {
    const next = catalog?.categories.find((c) => c.code === code);
    // Section 7 rule 5: preselect the only valid rail rather than ask.
    const only = next && next.subtypes.length === 1 ? next.subtypes[0].code : "";
    setNotice("");
    onChange({ ...value, category: code as PaymentMethodCategory, subtype: only, description: "" });
  }

  if (catalogError) {
    return <p role="alert" className="rounded-xl bg-rose-50 px-4 py-2.5 text-sm text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">{catalogError}</p>;
  }
  if (!catalog) {
    return <div className="h-12 animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" aria-label="Loading payment methods" />;
  }

  const categoryOptions: ComboboxOption[] = catalog.categories.map((c) => ({
    value: c.code,
    label: c.label,
    icon: <PaymentCategoryIcon category={c.code} />,
  }));
  const subtypeOptions: ComboboxOption[] = (category?.subtypes ?? []).map((s) => ({
    value: s.code,
    label: s.label,
    hint: s.region || undefined,
  }));
  const showSubtype = Boolean(category && category.subtypes.length > 0);
  const referenceHelperId = `${ids}-ref-help`;

  return (
    <div className="space-y-4">
      {notice && (
        <p role="status" className="rounded-xl bg-amber-50 px-4 py-2.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-200">
          {notice}
        </p>
      )}
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <FieldLabel id={`${ids}-cat`} text={categoryLabel} required />
          <OptionCombobox
            labelId={`${ids}-cat`}
            options={categoryOptions}
            value={value.category}
            onChange={selectCategory}
            placeholder="Select payment method..."
            invalid={Boolean(errors.category)}
            describedBy={errors.category ? `${ids}-cat-err` : undefined}
            required
          />
          <FieldError id={`${ids}-cat-err`} message={errors.category} />
        </div>

        {showSubtype && category && (
          <div>
            <FieldLabel id={`${ids}-sub`} text={category.subtypeLabel} required={category.subtypeRequired} optional={!category.subtypeRequired} />
            <OptionCombobox
              labelId={`${ids}-sub`}
              options={subtypeOptions}
              value={value.subtype}
              onChange={(code) => onChange({ ...value, subtype: code, description: "" })}
              placeholder={`Select ${category.subtypeLabel.toLowerCase()}`}
              invalid={Boolean(errors.subtype)}
              describedBy={errors.subtype ? `${ids}-sub-err` : undefined}
              required={category.subtypeRequired}
            />
            <FieldError id={`${ids}-sub-err`} message={errors.subtype} />
          </div>
        )}
      </div>

      {rules?.requiresDescription && (
        <div>
          <FieldLabel htmlFor={`${ids}-desc`} text="Method description" required />
          <input
            id={`${ids}-desc`}
            value={value.description}
            maxLength={200}
            onChange={(e) => onChange({ ...value, description: e.target.value })}
            aria-invalid={Boolean(errors.description) || undefined}
            aria-describedby={errors.description ? `${ids}-desc-err` : undefined}
            placeholder="e.g. Paid through my employer's housing scheme"
            className={cn(inputClass, errors.description && "ring-rose-400")}
          />
          <FieldError id={`${ids}-desc-err`} message={errors.description} />
        </div>
      )}

      <div>
        <FieldLabel
          htmlFor={`${ids}-ref`}
          text={rules?.referenceLabel ?? "Payment reference / transaction ID"}
          required={rules?.referencePolicy === "REQUIRED"}
          optional={rules?.referencePolicy !== "REQUIRED"}
        />
        <input
          id={`${ids}-ref`}
          value={value.reference}
          maxLength={255}
          autoComplete="off"
          onChange={(e) => onChange({ ...value, reference: e.target.value })}
          aria-invalid={Boolean(errors.reference) || undefined}
          aria-describedby={`${referenceHelperId}${errors.reference ? ` ${ids}-ref-err` : ""}`}
          placeholder="e.g. bank reference or transaction ID"
          className={cn(inputClass, errors.reference && "ring-rose-400")}
        />
        <p id={referenceHelperId} className="mt-1 flex gap-1.5 text-xs text-slate-500 dark:text-slate-400">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span>
            {rules?.referenceHelper ?? "Enter the reference provided by your bank or payment provider, if you have one."}
            {rules?.referencePolicy === "RECOMMENDED" && " Recommended -- it helps match the payment."}
          </span>
        </p>
        <FieldError id={`${ids}-ref-err`} message={errors.reference} />
      </div>
    </div>
  );
}

/** Right-hand "Payment Method Guidance" panel -- every option available for
 *  this rental, each expandable to show its rails. */
export function PaymentMethodGuidance({ catalog, className }: { catalog: PaymentMethodCatalog | null; className?: string }) {
  const [openCode, setOpenCode] = useState<string | null>(null);
  const ids = useId();
  return (
    <aside aria-labelledby={`${ids}-title`} className={cn("rounded-2xl bg-white p-5 ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10", className)}>
      <h3 id={`${ids}-title`} className="flex items-center gap-2 font-heading text-base font-bold text-primary-900 dark:text-white">
        <Info className="h-5 w-5 text-primary-600" aria-hidden="true" />
        Payment Method Guidance
      </h3>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Select the method you used to make the payment. The options shown are the ones available for this rental&apos;s country.
      </p>
      <ul className="mt-3 divide-y divide-slate-100 dark:divide-slate-800">
        {(catalog?.categories ?? []).map((c) => {
          const expanded = openCode === c.code;
          return (
            <li key={c.code}>
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={`${ids}-${c.code}`}
                onClick={() => setOpenCode(expanded ? null : c.code)}
                className="flex w-full items-start gap-3 py-3 text-left outline-none focus-visible:ring-2 focus-visible:ring-primary-400 rounded-lg"
              >
                <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-primary-700 dark:bg-slate-800 dark:text-primary-300" aria-hidden="true">
                  <PaymentCategoryIcon category={c.code} />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-semibold text-slate-800 dark:text-slate-100">{c.label}</span>
                  <span className="block text-xs text-slate-500 dark:text-slate-400">{c.description}</span>
                </span>
                <ChevronDown className={cn("mt-1 h-4 w-4 shrink-0 text-slate-400 transition-transform", expanded && "rotate-180")} aria-hidden="true" />
              </button>
              {expanded && (
                <div id={`${ids}-${c.code}`} className="pb-3 pl-11 text-xs text-slate-600 dark:text-slate-300">
                  {c.subtypes.length > 0 ? (
                    <>
                      <p className="font-semibold">{c.subtypeLabel}:</p>
                      <p>{c.subtypes.map((s) => (s.region ? `${s.label} (${s.region})` : s.label)).join(" · ")}</p>
                    </>
                  ) : (
                    <p>{c.referenceHelper}</p>
                  )}
                  {c.evidenceHelper && <p className="mt-1">{c.evidenceHelper}</p>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

export const EVIDENCE_ACCEPT = ".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png";
export const EVIDENCE_MAX_MB = 10;
const EVIDENCE_TYPES = ["application/pdf", "image/jpeg", "image/png"];

/** Section 8: PDF/JPG/PNG only, size limit shown before upload, a
 *  button-based picker (drag and drop is only an extra), and a warning
 *  never to upload card numbers or credentials. */
export function EvidenceUpload({
  file,
  onChange,
  helper,
}: {
  file: File | null;
  onChange: (file: File | null) => void;
  helper?: string;
}) {
  const ids = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");

  function accept(next: File | null | undefined) {
    if (!next) return;
    if (!EVIDENCE_TYPES.includes(next.type)) {
      setError("Upload a PDF, JPG or PNG file.");
      return;
    }
    if (next.size > EVIDENCE_MAX_MB * 1024 * 1024) {
      setError(`That file is larger than ${EVIDENCE_MAX_MB} MB.`);
      return;
    }
    setError("");
    onChange(next);
  }

  return (
    <div>
      <FieldLabel id={`${ids}-label`} text="Upload receipt / payment evidence" optional />
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          accept(e.dataTransfer.files?.[0]);
        }}
        className={cn(
          "rounded-xl border-2 border-dashed px-4 py-5 text-center text-xs transition-colors",
          dragging ? "border-primary-400 bg-primary-50 dark:bg-primary-500/10" : "border-slate-200 dark:border-slate-700"
        )}
      >
        {file ? (
          <div className="flex items-center justify-center gap-2 text-sm text-slate-700 dark:text-slate-200">
            <FileCheck className="h-4 w-4 text-emerald-600" aria-hidden="true" />
            <span className="truncate">{file.name}</span>
            <button
              type="button"
              onClick={() => onChange(null)}
              className="rounded-full p-1 text-slate-400 hover:text-rose-600"
              aria-label={`Remove ${file.name}`}
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <>
            <Upload className="mx-auto h-6 w-6 text-primary-600" aria-hidden="true" />
            <p className="mt-2 text-slate-600 dark:text-slate-300">
              Drag and drop a file here, or{" "}
              <button
                type="button"
                onClick={() => inputRef.current?.click()}
                className="font-semibold text-primary-700 underline-offset-2 hover:underline dark:text-primary-300"
                aria-describedby={`${ids}-hint`}
              >
                choose a file
              </button>
            </p>
          </>
        )}
        <p id={`${ids}-hint`} className="mt-1 text-slate-400">PDF, JPG or PNG · max {EVIDENCE_MAX_MB} MB</p>
        <input
          ref={inputRef}
          type="file"
          accept={EVIDENCE_ACCEPT}
          className="sr-only"
          tabIndex={-1}
          aria-labelledby={`${ids}-label`}
          onChange={(e) => {
            accept(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>
      {error && <p role="alert" className="mt-1 text-xs font-medium text-rose-600">{error}</p>}
      {helper && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{helper}</p>}
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Don&apos;t upload passwords, one-time codes, full card numbers, CVV codes or unrelated identity documents.
      </p>
    </div>
  );
}

/** Section 4 "copy lock" + provenance: the payment happened outside Zoiko. */
export function ExternalPaymentNotice({ role }: { role: "renter" | "host" | "return" }) {
  return (
    <div className="rounded-xl bg-primary-50/70 p-4 text-xs text-slate-700 ring-1 ring-primary-100 dark:bg-primary-500/10 dark:text-slate-200 dark:ring-primary-500/20">
      <p className="flex items-center gap-2 text-sm font-semibold text-primary-900 dark:text-white">
        <Info className="h-4 w-4" aria-hidden="true" /> Important
      </p>
      <ul className="mt-1.5 list-disc space-y-1 pl-5">
        {role === "renter" && <li>Only record payments you have made to the verified recipient (for example the landlord or agent).</li>}
        {role === "host" && <li>Only record money that you actually received for this rental.</li>}
        {role === "return" && <li>Only record money that you actually sent back to the renter.</li>}
        <li>
          Zoiko Rooms did not process or hold this payment. This record reflects information entered by an authorized party.
        </li>
        {role === "renter" && <li>Recording a payment does not confirm receipt. The recipient confirms it separately.</li>}
        {role === "host" && <li>Recorded by you as the host · paid outside Zoiko Rooms.</li>}
        {role === "return" && <li>The renter is asked to confirm it arrived.</li>}
      </ul>
    </div>
  );
}

/** A server warning the person must explicitly confirm before resending. */
function ConfirmWarning({
  title,
  message,
  actionLabel,
  onConfirm,
  submitting,
}: {
  title: string;
  message: string;
  actionLabel: string;
  onConfirm: () => void;
  submitting: boolean;
}) {
  return (
    <div role="alert" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-900 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-100 dark:ring-amber-500/30">
      <p className="flex items-center gap-2 font-semibold">
        <AlertTriangle className="h-4 w-4" aria-hidden="true" /> {title}
      </p>
      <p className="mt-1 text-xs">{message}</p>
      <button
        type="button"
        onClick={onConfirm}
        disabled={submitting}
        className="mt-2 rounded-lg bg-amber-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-amber-700 disabled:opacity-60"
      >
        {actionLabel}
      </button>
    </div>
  );
}

/** Section 8 duplicate warning: shown after a POSSIBLE_DUPLICATE 409, with
 *  an explicit "record it anyway" for a genuinely separate payment. */
export function DuplicateWarning({ message, onConfirm, submitting }: { message: string; onConfirm: () => void; submitting: boolean }) {
  return (
    <ConfirmWarning
      title="This may already be recorded"
      message={message}
      actionLabel="It's a separate payment -- record it"
      onConfirm={onConfirm}
      submitting={submitting}
    />
  );
}

/** Section 10: more than is owed is never applied silently -- confirming
 *  records it with the extra held for the recipient's decision. */
export function OverpaymentWarning({ message, onConfirm, submitting }: { message: string; onConfirm: () => void; submitting: boolean }) {
  return (
    <ConfirmWarning
      title="This is more than is owed"
      message={message}
      actionLabel="Yes, record the full amount"
      onConfirm={onConfirm}
      submitting={submitting}
    />
  );
}

/** Which server warnings the person has already confirmed for this form. */
export interface SubmitAcks {
  confirmNotDuplicate?: boolean;
  acknowledgeOverpayment?: boolean;
}

/** Routes a failed save to the right confirm-able warning; returns false
 *  for any other error. */
export function handleSubmitWarning(
  err: unknown,
  setDuplicate: (m: string) => void,
  setOverpayment: (m: string) => void
): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.code === "POSSIBLE_DUPLICATE") {
    setDuplicate(err.message);
    return true;
  }
  if (err.code === "OVERPAYMENT") {
    setOverpayment(err.message);
    return true;
  }
  return false;
}

/** Locked currency (Section 5.1): shown, never editable. */
export function LockedCurrency({ currency }: { currency: string }) {
  const ids = useId();
  return (
    <div>
      <FieldLabel htmlFor={`${ids}-cur`} text="Currency" />
      <input id={`${ids}-cur`} value={currency} readOnly aria-describedby={`${ids}-cur-help`} className={cn(inputClass, "cursor-not-allowed text-slate-500")} />
      <p id={`${ids}-cur-help`} className="mt-1 flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
        <Lock className="h-3.5 w-3.5" aria-hidden="true" /> Currency is set to the rental currency.
      </p>
    </div>
  );
}

export { FieldLabel, FieldError };
