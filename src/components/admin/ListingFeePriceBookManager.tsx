"use client";

import { useCallback, useEffect, useState } from "react";
import { Building2, Tag } from "lucide-react";
import { BillingEntity, ListingFeePolicy, ListingFeePriceStatus, MarketRelease } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch, ApiError } from "@/lib/api-client";
import { formatCurrency, formatDate } from "@/lib/utils";

const inputClass =
  "w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 disabled:opacity-60 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700";
const labelClass = "mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400";

const STATUS_TONE: Record<ListingFeePriceStatus, "success" | "warning" | "neutral"> = {
  ACTIVE: "success",
  APPROVED: "success",
  DRAFT: "warning",
  RETIRED: "neutral",
};

const today = () => new Date().toISOString().slice(0, 10);

const emptyPriceForm = {
  jurisdictionCode: "",
  effectiveFrom: today(),
  amount: "",
  currency: "GBP",
  taxRate: "0",
  taxBehavior: "EXCLUSIVE" as "EXCLUSIVE" | "INCLUSIVE",
  taxRuleReference: "",
  billingEntityId: "",
  disclosureText: "",
  refundEligible: false,
  refundWindowDays: "",
};

const emptyEntityForm = {
  code: "ZRG_INC",
  legalName: "Zoiko Realty Group Inc.",
  tradingName: "Zoiko Rooms",
  registeredAddress: "",
  companyRegistrationNumber: "",
  taxRegistrationType: "",
  taxRegistrationNumber: "",
  supportedMarkets: "",
  supportedCurrencies: "",
  effectiveFrom: today(),
  status: "ACTIVE" as "ACTIVE" | "INACTIVE",
};

const splitList = (value: string) =>
  value
    .split(",")
    .map((v) => v.trim())
    .filter(Boolean);

/**
 * ZR-PAY-CFG-001 Decisions 1, 2, 5 and 6: the Listing Fee Price Book and the
 * Billing Entity Registry. A price is created as a draft and only becomes
 * chargeable once approved -- approval is refused until the market has a
 * billing entity approved for its currency and a Finance/Tax-approved tax
 * rule. Approving a new version retires the previous active price. Quotes
 * are valid for a fixed 30 minutes.
 */
export function ListingFeePriceBookManager({ showToast }: { showToast: (message: string) => void }) {
  const [prices, setPrices] = useState<ListingFeePolicy[]>([]);
  const [entities, setEntities] = useState<BillingEntity[]>([]);
  const [markets, setMarkets] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const [priceModalOpen, setPriceModalOpen] = useState(false);
  const [priceForm, setPriceForm] = useState(emptyPriceForm);
  const [editingPriceId, setEditingPriceId] = useState<number | null>(null);

  const [entityModalOpen, setEntityModalOpen] = useState(false);
  const [entityForm, setEntityForm] = useState(emptyEntityForm);
  const [editingEntityId, setEditingEntityId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const [priceData, entityData, releases] = await Promise.all([
        apiClientFetch<ListingFeePolicy[]>("/api/finance/listing-fees/policies"),
        apiClientFetch<BillingEntity[]>("/api/finance/listing-fees/billing-entities"),
        apiClientFetch<MarketRelease[]>("/api/market-releases"),
      ]);
      setPrices(priceData);
      setEntities(entityData);
      setMarkets(releases.map((r) => r.jurisdiction).sort());
    } catch {
      showToast("Failed to load the Listing Fee Price Book");
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  const entityById = (id: number | null) => entities.find((e) => e.id === id);

  function openCreatePrice() {
    setEditingPriceId(null);
    setPriceForm({ ...emptyPriceForm, jurisdictionCode: markets[0] ?? "", effectiveFrom: today() });
    setPriceModalOpen(true);
  }

  function openEditPrice(price: ListingFeePolicy) {
    setEditingPriceId(price.id);
    setPriceForm({
      jurisdictionCode: price.jurisdictionCode,
      effectiveFrom: price.effectiveFrom.slice(0, 10),
      amount: String(price.amount),
      currency: price.currency,
      taxRate: String(price.taxRate),
      taxBehavior: price.taxBehavior,
      taxRuleReference: price.taxRuleReference,
      billingEntityId: price.billingEntityId ? String(price.billingEntityId) : "",
      disclosureText: price.disclosureText,
      refundEligible: price.refundEligible,
      refundWindowDays: price.refundWindowDays == null ? "" : String(price.refundWindowDays),
    });
    setPriceModalOpen(true);
  }

  async function submitPrice(e: React.FormEvent) {
    e.preventDefault();
    const body = {
      effectiveFrom: priceForm.effectiveFrom,
      amount: Number(priceForm.amount),
      currency: priceForm.currency.toUpperCase(),
      taxRate: Number(priceForm.taxRate),
      taxBehavior: priceForm.taxBehavior,
      taxRuleReference: priceForm.taxRuleReference.trim(),
      billingEntityId: priceForm.billingEntityId ? Number(priceForm.billingEntityId) : null,
      disclosureText: priceForm.disclosureText,
      refundEligible: priceForm.refundEligible,
      refundWindowDays: priceForm.refundWindowDays ? Number(priceForm.refundWindowDays) : null,
    };
    setBusy("price");
    try {
      if (editingPriceId) {
        await apiClientFetch(`/api/finance/listing-fees/policies/${editingPriceId}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
        showToast("Draft price updated");
      } else {
        await apiClientFetch("/api/finance/listing-fees/policies", {
          method: "POST",
          body: JSON.stringify({ ...body, jurisdictionCode: priceForm.jurisdictionCode }),
        });
        showToast("Draft price created — approve it to make it chargeable");
      }
      setPriceModalOpen(false);
      await load();
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to save the price");
    } finally {
      setBusy(null);
    }
  }

  async function priceAction(price: ListingFeePolicy, action: "approve" | "retire") {
    setBusy(`${action}:${price.id}`);
    try {
      await apiClientFetch(`/api/finance/listing-fees/policies/${price.id}/${action}`, { method: "POST" });
      showToast(action === "approve" ? `${price.jurisdictionCode} v${price.version} is now the active price` : "Price retired");
      await load();
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : `Failed to ${action} the price`);
    } finally {
      setBusy(null);
    }
  }

  function openCreateEntity() {
    setEditingEntityId(null);
    setEntityForm({ ...emptyEntityForm, effectiveFrom: today() });
    setEntityModalOpen(true);
  }

  function openEditEntity(entity: BillingEntity) {
    setEditingEntityId(entity.id);
    setEntityForm({
      code: entity.code,
      legalName: entity.legalName,
      tradingName: entity.tradingName,
      registeredAddress: entity.registeredAddress,
      companyRegistrationNumber: entity.companyRegistrationNumber,
      taxRegistrationType: entity.taxRegistrationType,
      taxRegistrationNumber: entity.taxRegistrationNumber,
      supportedMarkets: entity.supportedMarkets.join(", "),
      supportedCurrencies: entity.supportedCurrencies.join(", "),
      effectiveFrom: entity.effectiveFrom.slice(0, 10),
      status: entity.status,
    });
    setEntityModalOpen(true);
  }

  async function submitEntity(e: React.FormEvent) {
    e.preventDefault();
    const body = {
      legalName: entityForm.legalName.trim(),
      tradingName: entityForm.tradingName.trim(),
      registeredAddress: entityForm.registeredAddress.trim(),
      companyRegistrationNumber: entityForm.companyRegistrationNumber.trim(),
      taxRegistrationType: entityForm.taxRegistrationType.trim(),
      taxRegistrationNumber: entityForm.taxRegistrationNumber.trim(),
      supportedMarkets: splitList(entityForm.supportedMarkets),
      supportedCurrencies: splitList(entityForm.supportedCurrencies),
      effectiveFrom: entityForm.effectiveFrom,
      status: entityForm.status,
    };
    setBusy("entity");
    try {
      if (editingEntityId) {
        await apiClientFetch(`/api/finance/listing-fees/billing-entities/${editingEntityId}`, {
          method: "PUT",
          body: JSON.stringify(body),
        });
        showToast("Billing entity updated");
      } else {
        await apiClientFetch("/api/finance/listing-fees/billing-entities", {
          method: "POST",
          body: JSON.stringify({ ...body, code: entityForm.code.trim() }),
        });
        showToast("Billing entity registered");
      }
      setEntityModalOpen(false);
      await load();
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to save the billing entity");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Building2 className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" aria-hidden="true" />
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Billing entities</h2>
          </div>
          <Button size="sm" variant="outline" onClick={openCreateEntity}>
            New entity
          </Button>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          The legal Zoiko entity that issues Listing Fee receipts, with its registration and tax details. Baseline: Zoiko
          Realty Group Inc., trading as Zoiko Rooms. A price can only be approved for a market and currency its entity
          supports.
        </p>
        <div className="mt-4 space-y-2">
          {entities.map((entity) => (
            <div
              key={entity.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800"
            >
              <div className="min-w-0">
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {entity.code} · {entity.legalName}
                  {entity.tradingName && entity.tradingName !== entity.legalName && ` (trading as ${entity.tradingName})`}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Markets: {entity.supportedMarkets.join(", ") || "none"} · Currencies:{" "}
                  {entity.supportedCurrencies.join(", ") || "none"}
                  {entity.taxRegistrationNumber && ` · ${entity.taxRegistrationType || "Tax"} ${entity.taxRegistrationNumber}`}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={entity.status === "ACTIVE" ? "success" : "neutral"}>{entity.status}</Badge>
                <Button size="sm" variant="outline" onClick={() => openEditEntity(entity)}>
                  Edit
                </Button>
              </div>
            </div>
          ))}
          {entities.length === 0 && (
            <p className="text-sm text-slate-400">
              No billing entity yet — register one before any Listing Fee price can be approved.
            </p>
          )}
        </div>
      </section>

      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Tag className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" aria-hidden="true" />
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Listing Fee Price Book</h2>
          </div>
          <Button size="sm" variant="accent" onClick={openCreatePrice} disabled={markets.length === 0}>
            New price
          </Button>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          The Listing Fee is the only money Zoiko Rooms collects. Each market needs an approved active price before hosts
          there can pay and publish — without one, listings stay blocked with &quot;Listing fee currently unavailable in
          this market.&quot; New prices start as drafts; approving one retires the previous active price. Quotes are valid
          for 30 minutes.
        </p>
        <div className="mt-4 space-y-2">
          {prices.map((price) => {
            const entity = entityById(price.billingEntityId);
            return (
              <div
                key={price.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800"
              >
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-primary-900 dark:text-white">
                    {price.jurisdictionCode} · v{price.version} · {formatCurrency(price.amount, price.currency)}{" "}
                    <span className="font-normal text-slate-500 dark:text-slate-400">
                      {price.taxBehavior === "INCLUSIVE" ? "incl." : "+"} {(price.taxRate * 100).toFixed(2)}% tax
                    </span>
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {entity ? entity.code : "No billing entity"} · tax rule {price.taxRuleReference || "not set"} ·
                    effective {formatDate(price.effectiveFrom)}
                    {price.effectiveTo ? ` – ${formatDate(price.effectiveTo)}` : " onward"} · {price.environment}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  {price.status === "ACTIVE" && (!entity || !price.taxRuleReference.trim()) && (
                    <Badge tone="danger">Not chargeable — missing {!entity ? "billing entity" : "tax rule"}</Badge>
                  )}
                  <Badge tone={STATUS_TONE[price.status] ?? "neutral"}>{price.status}</Badge>
                  {price.status === "DRAFT" && (
                    <>
                      <Button size="sm" variant="outline" onClick={() => openEditPrice(price)}>
                        Edit
                      </Button>
                      <Button
                        size="sm"
                        variant="primary"
                        loading={busy === `approve:${price.id}`}
                        onClick={() => priceAction(price, "approve")}
                      >
                        Approve
                      </Button>
                    </>
                  )}
                  {price.status !== "RETIRED" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      loading={busy === `retire:${price.id}`}
                      onClick={() => priceAction(price, "retire")}
                    >
                      Retire
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
          {prices.length === 0 && (
            <p className="text-sm text-slate-400">
              No Listing Fee prices yet — hosts can&apos;t pay or publish in any market until one is approved.
            </p>
          )}
          {markets.length === 0 && (
            <p className="text-xs text-amber-600">Create a market release first — prices are set per market.</p>
          )}
        </div>
      </section>

      <Modal
        open={priceModalOpen}
        onClose={() => setPriceModalOpen(false)}
        title={editingPriceId ? "Edit draft price" : "New Listing Fee price"}
      >
        <form onSubmit={submitPrice} className="space-y-3.5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Market</label>
              <select
                value={priceForm.jurisdictionCode}
                onChange={(e) => setPriceForm((f) => ({ ...f, jurisdictionCode: e.target.value }))}
                disabled={Boolean(editingPriceId)}
                className={inputClass}
                required
              >
                {markets.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className={labelClass}>Effective from</label>
              <input
                type="date"
                value={priceForm.effectiveFrom}
                onChange={(e) => setPriceForm((f) => ({ ...f, effectiveFrom: e.target.value }))}
                className={inputClass}
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Amount</label>
              <input
                type="number"
                min={0}
                step="0.01"
                value={priceForm.amount}
                onChange={(e) => setPriceForm((f) => ({ ...f, amount: e.target.value }))}
                className={inputClass}
                required
              />
            </div>
            <div>
              <label className={labelClass}>Currency</label>
              <input
                value={priceForm.currency}
                onChange={(e) => setPriceForm((f) => ({ ...f, currency: e.target.value }))}
                maxLength={3}
                placeholder="GBP"
                className={`${inputClass} uppercase`}
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>
                Tax rate <span className="font-normal normal-case text-slate-400">(0.2 = 20%)</span>
              </label>
              <input
                type="number"
                min={0}
                max={1}
                step="0.0001"
                value={priceForm.taxRate}
                onChange={(e) => setPriceForm((f) => ({ ...f, taxRate: e.target.value }))}
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Tax behavior</label>
              <select
                value={priceForm.taxBehavior}
                onChange={(e) =>
                  setPriceForm((f) => ({ ...f, taxBehavior: e.target.value as "EXCLUSIVE" | "INCLUSIVE" }))
                }
                className={inputClass}
              >
                <option value="EXCLUSIVE">Exclusive (tax added on top)</option>
                <option value="INCLUSIVE">Inclusive (tax already in price)</option>
              </select>
            </div>
          </div>
          <div>
            <label className={labelClass}>
              Tax rule reference <span className="font-normal normal-case text-slate-400">(Finance/Tax approved)</span>
            </label>
            <input
              value={priceForm.taxRuleReference}
              onChange={(e) => setPriceForm((f) => ({ ...f, taxRuleReference: e.target.value }))}
              placeholder="e.g. UK-VAT-STANDARD-2026"
              className={inputClass}
            />
          </div>
          <div>
            <label className={labelClass}>Billing entity</label>
            <select
              value={priceForm.billingEntityId}
              onChange={(e) => setPriceForm((f) => ({ ...f, billingEntityId: e.target.value }))}
              className={inputClass}
            >
              <option value="">Select a billing entity</option>
              {entities.map((entity) => (
                <option key={entity.id} value={entity.id}>
                  {entity.code} — {entity.legalName}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={labelClass}>
              Disclosure text <span className="font-normal normal-case text-slate-400">(shown before checkout)</span>
            </label>
            <textarea
              value={priceForm.disclosureText}
              onChange={(e) => setPriceForm((f) => ({ ...f, disclosureText: e.target.value }))}
              rows={2}
              className={`${inputClass} resize-none`}
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              id="price-refund-eligible"
              type="checkbox"
              checked={priceForm.refundEligible}
              onChange={(e) => setPriceForm((f) => ({ ...f, refundEligible: e.target.checked }))}
              className="h-4 w-4 rounded border-slate-300 text-primary-600 focus:ring-primary-400"
            />
            <label htmlFor="price-refund-eligible" className="text-sm text-slate-700 dark:text-slate-200">
              Refund eligible
            </label>
          </div>
          {priceForm.refundEligible && (
            <div>
              <label className={labelClass}>
                Refund window (days) <span className="font-normal normal-case text-slate-400">(blank = no limit)</span>
              </label>
              <input
                type="number"
                min={0}
                value={priceForm.refundWindowDays}
                onChange={(e) => setPriceForm((f) => ({ ...f, refundWindowDays: e.target.value }))}
                className={inputClass}
              />
            </div>
          )}
          <Button type="submit" variant="primary" fullWidth loading={busy === "price"}>
            {editingPriceId ? "Save draft" : "Create draft price"}
          </Button>
        </form>
      </Modal>

      <Modal
        open={entityModalOpen}
        onClose={() => setEntityModalOpen(false)}
        title={editingEntityId ? "Edit billing entity" : "New billing entity"}
      >
        <form onSubmit={submitEntity} className="space-y-3.5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Code</label>
              <input
                value={entityForm.code}
                onChange={(e) => setEntityForm((f) => ({ ...f, code: e.target.value }))}
                disabled={Boolean(editingEntityId)}
                className={`${inputClass} uppercase`}
                required
              />
            </div>
            <div>
              <label className={labelClass}>Status</label>
              <select
                value={entityForm.status}
                onChange={(e) => setEntityForm((f) => ({ ...f, status: e.target.value as "ACTIVE" | "INACTIVE" }))}
                className={inputClass}
              >
                <option value="ACTIVE">Active</option>
                <option value="INACTIVE">Inactive</option>
              </select>
            </div>
          </div>
          <div>
            <label className={labelClass}>Legal name</label>
            <input
              value={entityForm.legalName}
              onChange={(e) => setEntityForm((f) => ({ ...f, legalName: e.target.value }))}
              className={inputClass}
              required
            />
          </div>
          <div>
            <label className={labelClass}>Trading name</label>
            <input
              value={entityForm.tradingName}
              onChange={(e) => setEntityForm((f) => ({ ...f, tradingName: e.target.value }))}
              className={inputClass}
            />
          </div>
          <div>
            <label className={labelClass}>Registered address</label>
            <input
              value={entityForm.registeredAddress}
              onChange={(e) => setEntityForm((f) => ({ ...f, registeredAddress: e.target.value }))}
              className={inputClass}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Company registration no.</label>
              <input
                value={entityForm.companyRegistrationNumber}
                onChange={(e) => setEntityForm((f) => ({ ...f, companyRegistrationNumber: e.target.value }))}
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Effective from</label>
              <input
                type="date"
                value={entityForm.effectiveFrom}
                onChange={(e) => setEntityForm((f) => ({ ...f, effectiveFrom: e.target.value }))}
                className={inputClass}
                required
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className={labelClass}>Tax registration type</label>
              <input
                value={entityForm.taxRegistrationType}
                onChange={(e) => setEntityForm((f) => ({ ...f, taxRegistrationType: e.target.value }))}
                placeholder="VAT, GST…"
                className={inputClass}
              />
            </div>
            <div>
              <label className={labelClass}>Tax registration no.</label>
              <input
                value={entityForm.taxRegistrationNumber}
                onChange={(e) => setEntityForm((f) => ({ ...f, taxRegistrationNumber: e.target.value }))}
                className={inputClass}
              />
            </div>
          </div>
          <div>
            <label className={labelClass}>
              Supported markets <span className="font-normal normal-case text-slate-400">(comma-separated)</span>
            </label>
            <input
              value={entityForm.supportedMarkets}
              onChange={(e) => setEntityForm((f) => ({ ...f, supportedMarkets: e.target.value }))}
              placeholder={markets.join(", ") || "England"}
              className={inputClass}
            />
          </div>
          <div>
            <label className={labelClass}>
              Supported currencies <span className="font-normal normal-case text-slate-400">(comma-separated)</span>
            </label>
            <input
              value={entityForm.supportedCurrencies}
              onChange={(e) => setEntityForm((f) => ({ ...f, supportedCurrencies: e.target.value }))}
              placeholder="GBP"
              className={`${inputClass} uppercase`}
            />
          </div>
          <Button type="submit" variant="primary" fullWidth loading={busy === "entity"}>
            {editingEntityId ? "Save entity" : "Register entity"}
          </Button>
        </form>
      </Modal>
    </>
  );
}
