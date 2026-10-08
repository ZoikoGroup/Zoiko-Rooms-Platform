"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Globe2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { errorMessage } from "@/lib/user-api";
import {
  AuthorityPack, AuthorityPackRequirement, AuthorityRoute, listAuthorityPacks, updateAuthorityPack,
} from "@/lib/authority-verification";

const ROUTE_LABEL: Record<AuthorityRoute, string> = {
  OWNER: "Owner / co-owner", AGENT: "Agent / property manager", SUBLET: "Tenant / subletter",
};
const fieldClass = "w-full rounded-lg border border-slate-300 px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-800";

function lines(values: string[] | undefined) {
  return (values ?? []).join("\n");
}

function parseLines(text: string) {
  return text.split("\n").map((s) => s.trim()).filter(Boolean);
}

type DraftDocument = { label: string; keywordsText: string };
type Draft = {
  requirements: Record<AuthorityRoute, (AuthorityPackRequirement & { docs: DraftDocument[] })[]>;
  subletConsentRequired: boolean;
  coOwnerConsentRequired: boolean;
  defaultValidityDays: number;
  expiringSoonDays: number;
  listingControl: "SUSPEND" | "NONE";
};

function toDraft(pack: AuthorityPack): Draft {
  const requirements = Object.fromEntries((Object.keys(pack.requirements) as AuthorityRoute[]).map((route) => [
    route, pack.requirements[route].map((r) => ({
      ...r, docs: (r.documents ?? []).map((d) => ({ label: d.label, keywordsText: lines(d.keywords) })),
    })),
  ])) as Draft["requirements"];
  return {
    requirements, subletConsentRequired: pack.subletConsentRequired, coOwnerConsentRequired: pack.coOwnerConsentRequired,
    defaultValidityDays: pack.defaultValidityDays, expiringSoonDays: pack.expiringSoonDays, listingControl: pack.listingControl,
  };
}

/**
 * ZR-AUTHORITY-002 Section 4 -- Country document rules. Trust & Safety edits
 * what each country accepts per role: the examples hosts see, the keywords
 * an uploaded document (text layer or OCR) must contain, and the pack
 * settings. Every save is a new pack version; earlier versions are kept.
 * Requirements themselves can't be added or removed here -- the decision
 * logic relies on them.
 */
export function AuthorityPackEditor() {
  const [packs, setPacks] = useState<AuthorityPack[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async (keepCountry?: string) => {
    const rows = await listAuthorityPacks();
    setPacks(rows);
    const pick = rows.find((p) => p.countryCode === keepCountry) ?? rows.find((p) => p.countryCode === "IN") ?? rows[0];
    if (pick) {
      setSelectedId(pick.id);
      setDraft(toDraft(pick));
    }
  }, []);

  useEffect(() => { load().catch((err) => setError(errorMessage(err, "Could not load the country rules."))); }, [load]);

  const pack = useMemo(() => packs.find((p) => p.id === selectedId) ?? null, [packs, selectedId]);

  function select(id: number) {
    const next = packs.find((p) => p.id === id);
    setSelectedId(id);
    setDraft(next ? toDraft(next) : null);
    setMessage("");
    setError("");
  }

  function updateReq(route: AuthorityRoute, index: number, patch: Partial<Draft["requirements"][AuthorityRoute][number]>) {
    setDraft((d) => d && {
      ...d, requirements: { ...d.requirements, [route]: d.requirements[route].map((r, i) => (i === index ? { ...r, ...patch } : r)) },
    });
  }

  async function save() {
    if (!pack || !draft) return;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const requirements = Object.fromEntries((Object.keys(draft.requirements) as AuthorityRoute[]).map((route) => [
        route, draft.requirements[route].map((r) => {
          const { docs, ...rest } = r;
          const documents = docs.map((d) => ({ label: d.label.trim(), keywords: parseLines(d.keywordsText) }));
          return { ...rest, documents, accepted_examples: documents.map((d) => d.label) };
        }),
      ])) as Record<AuthorityRoute, AuthorityPackRequirement[]>;
      const saved = await updateAuthorityPack(pack.id, {
        requirements, subletConsentRequired: draft.subletConsentRequired, coOwnerConsentRequired: draft.coOwnerConsentRequired,
        defaultValidityDays: draft.defaultValidityDays, expiringSoonDays: draft.expiringSoonDays, listingControl: draft.listingControl,
      });
      await load(pack.countryCode);
      setMessage(`Saved as version ${saved.version}. New verifications use it; earlier ones keep their version.`);
    } catch (err) {
      setError(errorMessage(err, "Could not save these rules."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="rounded-xl bg-slate-50 p-3 text-sm dark:bg-slate-800/60">
      <summary className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-slate-700 dark:text-slate-200">
        <Globe2 className="h-4 w-4" aria-hidden="true" /> Country document rules
      </summary>
      {!pack || !draft ? (
        <p className="mt-3 text-xs text-slate-400" role={error ? "alert" : "status"}>{error || "Loading..."}</p>
      ) : (
        <div className="mt-3 space-y-4">
          <label className="flex flex-wrap items-center gap-2 text-xs">
            Country
            <select className={`${fieldClass} w-auto`} value={pack.id} onChange={(e) => select(Number(e.target.value))}>
              {packs.map((p) => (
                <option key={p.id} value={p.id}>{p.countryCode === "*" ? "Other countries" : `${p.countryName} (${p.countryCode})`}</option>
              ))}
            </select>
            <span className="text-slate-400">version {pack.version}</span>
          </label>

          <fieldset className="grid gap-3 text-xs sm:grid-cols-2 lg:grid-cols-3">
            <legend className="sr-only">Pack settings</legend>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={draft.subletConsentRequired}
                     onChange={(e) => setDraft({ ...draft, subletConsentRequired: e.target.checked })} />
              Landlord&apos;s permission is required to sublet
            </label>
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={draft.coOwnerConsentRequired}
                     onChange={(e) => setDraft({ ...draft, coOwnerConsentRequired: e.target.checked })} />
              Co-owners must consent
            </label>
            <label className="flex items-center gap-2">
              When authority ends
              <select className={`${fieldClass} w-auto`} value={draft.listingControl}
                      onChange={(e) => setDraft({ ...draft, listingControl: e.target.value as Draft["listingControl"] })}>
                <option value="SUSPEND">pause live listings</option>
                <option value="NONE">leave listings live</option>
              </select>
            </label>
            <label className="flex items-center gap-2">
              Valid for
              <input type="number" min={30} max={1825} className={`${fieldClass} w-24`} value={draft.defaultValidityDays}
                     onChange={(e) => setDraft({ ...draft, defaultValidityDays: Number(e.target.value) })} /> days
            </label>
            <label className="flex items-center gap-2">
              Warn
              <input type="number" min={1} max={180} className={`${fieldClass} w-20`} value={draft.expiringSoonDays}
                     onChange={(e) => setDraft({ ...draft, expiringSoonDays: Number(e.target.value) })} /> days before expiry
            </label>
          </fieldset>

          {(Object.keys(draft.requirements) as AuthorityRoute[]).map((route) => (
            <section key={route} className="space-y-2" aria-labelledby={`pack-route-${route}`}>
              <h3 id={`pack-route-${route}`} className="text-xs font-bold uppercase tracking-wide text-slate-500">{ROUTE_LABEL[route]}</h3>
              {draft.requirements[route].map((req, i) => (
                <div key={req.requirement_id} className="space-y-2 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-700 dark:bg-slate-900">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <input className={`${fieldClass} max-w-md font-semibold`} value={req.title} aria-label={`${req.requirement_id} title`}
                           onChange={(e) => updateReq(route, i, { title: e.target.value })} />
                    <span className="flex gap-3 text-xs">
                      <label className="flex items-center gap-1">
                        <input type="checkbox" checked={req.required} onChange={(e) => updateReq(route, i, { required: e.target.checked })} />
                        Required
                      </label>
                      <label className="flex items-center gap-1">
                        <input type="checkbox" checked={req.owner_confirmation}
                               onChange={(e) => updateReq(route, i, { owner_confirmation: e.target.checked })} />
                        Owner / landlord can confirm by email
                      </label>
                    </span>
                  </div>
                  <p className="text-xs text-slate-500">
                    Accepted documents, shown to hosts as a list. An upload is recognised as the first document whose
                    phrase appears in its text (one phrase per line; every word of a phrase must appear).
                  </p>
                  <ul className="space-y-2">
                    {req.docs.map((doc, d) => (
                      <li key={d} className="grid gap-2 rounded-md bg-slate-50 p-2 sm:grid-cols-[1fr_1fr_auto] dark:bg-slate-800/60">
                        <input className={fieldClass} value={doc.label} placeholder="Document name"
                               aria-label={`${req.requirement_id} document ${d + 1} name`}
                               onChange={(e) => updateReq(route, i, {
                                 docs: req.docs.map((x, j) => (j === d ? { ...x, label: e.target.value } : x)),
                               })} />
                        <textarea rows={2} className={fieldClass} value={doc.keywordsText} placeholder="Identifying phrases, one per line"
                                  aria-label={`${req.requirement_id} document ${d + 1} phrases`}
                                  onChange={(e) => updateReq(route, i, {
                                    docs: req.docs.map((x, j) => (j === d ? { ...x, keywordsText: e.target.value } : x)),
                                  })} />
                        <button type="button" className="self-start p-2 text-slate-400 hover:text-accent-600 disabled:opacity-40"
                                aria-label={`Remove ${doc.label || "document"}`} disabled={req.docs.length === 1}
                                onClick={() => updateReq(route, i, { docs: req.docs.filter((_, j) => j !== d) })}>
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                        </button>
                      </li>
                    ))}
                  </ul>
                  <Button size="sm" variant="ghost" onClick={() => updateReq(route, i, { docs: [...req.docs, { label: "", keywordsText: "" }] })}>
                    <Plus className="h-3.5 w-3.5" aria-hidden="true" /> Add document
                  </Button>
                  <p className="text-[11px] text-slate-400">{req.requirement_id} · {req.evidence_class}</p>
                </div>
              ))}
            </section>
          ))}

          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" onClick={save} loading={busy} disabled={busy}>Save as new version</Button>
            <Button size="sm" variant="ghost" onClick={() => setDraft(toDraft(pack))} disabled={busy}>Discard changes</Button>
            <span aria-live="polite" className="text-xs">
              {message && <span className="text-emerald-700 dark:text-emerald-300">{message}</span>}
              {error && <span className="text-accent-700" role="alert">{error}</span>}
            </span>
          </div>
        </div>
      )}
    </details>
  );
}
