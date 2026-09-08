"use client";

import { useState } from "react";
import { FinanceLedgerManager } from "@/components/admin/FinanceLedgerManager";
import { FinanceOpsManager } from "@/components/admin/FinanceOpsManager";

const TABS = [
  { key: "ledger", label: "Obligations & Deposits" },
  { key: "ops", label: "Payouts, Refunds & Reconciliation" },
] as const;

export function FinanceManager() {
  const [tab, setTab] = useState<(typeof TABS)[number]["key"]>("ledger");

  return (
    <div>
      <div className="mb-5 flex gap-2">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`rounded-full px-4 py-2 text-sm font-semibold transition-all duration-200 ${
              tab === t.key
                ? "bg-primary-700 text-white shadow-md shadow-primary-900/25"
                : "bg-slate-50 text-slate-500 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "ledger" ? <FinanceLedgerManager /> : <FinanceOpsManager />}
    </div>
  );
}
