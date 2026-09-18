"use client";

import { useState } from "react";
import { CheckCircle2, FileCheck2 } from "lucide-react";
import { PropertyComplianceCredential } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { apiClientFetch } from "@/lib/api-client";
import { propertyComplianceCredentialStatusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";

/** ZR-ENG-CLR-012 Section 14: Property Compliance Credential Registry --
 * "structured credentials, not attachment folders." Looked up by room ID
 * (no room picker yet -- an admin working a specific listing already has
 * the room ID from that listing's own admin view). Which requirement codes
 * are actually mandatory anywhere is a MarketPolicyPack config decision
 * (never hard-coded here), so issuing a credential accepts any code an
 * admin types -- consistent with the doc's "resolver returns permitted
 * options, not a static global checklist" doctrine. */
export function PropertyComplianceManager() {
  const [roomIdInput, setRoomIdInput] = useState("");
  const [roomId, setRoomId] = useState<number | null>(null);
  const [credentials, setCredentials] = useState<PropertyComplianceCredential[]>([]);
  const [loading, setLoading] = useState(false);
  const [toast, setToast] = useState("");

  const [requirementCode, setRequirementCode] = useState("");
  const [issuerSource, setIssuerSource] = useState("");
  const [issuing, setIssuing] = useState(false);
  const [revokingId, setRevokingId] = useState<number | null>(null);
  const [verifyingId, setVerifyingId] = useState<number | null>(null);
  const [suspendingId, setSuspendingId] = useState<number | null>(null);
  const [resumingId, setResumingId] = useState<number | null>(null);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  async function loadForRoom(id: number) {
    setLoading(true);
    try {
      const data = await apiClientFetch<PropertyComplianceCredential[]>(`/api/verification/property-compliance-credentials/room/${id}`);
      setCredentials(data);
      setRoomId(id);
    } catch {
      showToast("Failed to load credentials for that room");
    } finally {
      setLoading(false);
    }
  }

  function handleLookup() {
    const id = Number(roomIdInput);
    if (!Number.isInteger(id) || id <= 0) return showToast("Enter a valid room ID");
    loadForRoom(id);
  }

  async function handleIssue() {
    if (!roomId) return;
    if (!requirementCode.trim()) return showToast("Enter a requirement code");
    setIssuing(true);
    try {
      await apiClientFetch("/api/verification/property-compliance-credentials", {
        method: "POST",
        body: JSON.stringify({ roomId, requirementCode: requirementCode.trim().toUpperCase(), issuerSource: issuerSource.trim() }),
      });
      showToast("Credential issued.");
      setRequirementCode("");
      setIssuerSource("");
      await loadForRoom(roomId);
    } catch {
      showToast("Failed to issue credential");
    } finally {
      setIssuing(false);
    }
  }

  async function handleRevoke(credentialId: number) {
    if (!roomId) return;
    setRevokingId(credentialId);
    try {
      await apiClientFetch(`/api/verification/property-compliance-credentials/${credentialId}/revoke`, {
        method: "POST",
        body: JSON.stringify({ reason: "Revoked by admin" }),
      });
      showToast("Credential revoked.");
      await loadForRoom(roomId);
    } catch {
      showToast("Failed to revoke credential");
    } finally {
      setRevokingId(null);
    }
  }

  async function handleVerifyDeclared(credentialId: number) {
    if (!roomId) return;
    setVerifyingId(credentialId);
    try {
      await apiClientFetch(`/api/verification/property-compliance-credentials/${credentialId}/verify-declared`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      showToast("Credential verified — now VALID.");
      await loadForRoom(roomId);
    } catch {
      showToast("Failed to verify this credential — super admin access required");
    } finally {
      setVerifyingId(null);
    }
  }

  async function handleSuspend(credentialId: number) {
    if (!roomId) return;
    setSuspendingId(credentialId);
    try {
      await apiClientFetch(`/api/verification/property-compliance-credentials/${credentialId}/suspend`, {
        method: "POST",
        body: JSON.stringify({ reason: "Suspended by admin pending investigation" }),
      });
      showToast("Credential suspended.");
      await loadForRoom(roomId);
    } catch {
      showToast("Failed to suspend credential");
    } finally {
      setSuspendingId(null);
    }
  }

  async function handleResume(credentialId: number) {
    if (!roomId) return;
    setResumingId(credentialId);
    try {
      await apiClientFetch(`/api/verification/property-compliance-credentials/${credentialId}/resume`, { method: "POST" });
      showToast("Credential resumed — VALID again.");
      await loadForRoom(roomId);
    } catch {
      showToast("Failed to resume credential");
    } finally {
      setResumingId(null);
    }
  }

  return (
    <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
      <div className="flex items-center gap-2">
        <FileCheck2 className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
        <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Property Compliance Credentials</h2>
      </div>
      <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
        Structured, room-scoped compliance credentials (e.g. gas safety, EPC, HMO license) — never a hard-coded
        document list. Which codes are mandatory is a per-jurisdiction policy decision, configured separately.
        A Host&apos;s own self-declared credential starts as <b>UNDER_REVIEW</b> and only counts toward the publish/
        booking gate once a super admin verifies it to <b>VALID</b>. <b>EXPIRING</b> is shown automatically within
        30 days of expiry; <b>SUSPENDED</b> is a temporary hold that can be resumed.
      </p>

      <div className="mt-4 flex flex-wrap items-end gap-2">
        <div>
          <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Room ID</label>
          <input
            type="number"
            value={roomIdInput}
            onChange={(e) => setRoomIdInput(e.target.value)}
            className="w-32 rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
        </div>
        <Button size="sm" variant="outline" loading={loading} onClick={handleLookup}>
          Look up
        </Button>
      </div>

      {roomId !== null && (
        <div className="mt-4 space-y-4">
          <div className="flex flex-wrap items-end gap-2 rounded-xl bg-slate-50 p-3 dark:bg-slate-800">
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Requirement code</label>
              <input
                value={requirementCode}
                onChange={(e) => setRequirementCode(e.target.value)}
                placeholder="e.g. GAS_SAFETY_CERT"
                className="w-56 rounded-xl bg-white px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold text-slate-500 dark:text-slate-400">Issuer / source</label>
              <input
                value={issuerSource}
                onChange={(e) => setIssuerSource(e.target.value)}
                placeholder="e.g. Gas Safe registered engineer"
                className="w-64 rounded-xl bg-white px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <Button size="sm" variant="primary" loading={issuing} onClick={handleIssue}>
              Issue credential
            </Button>
          </div>

          <div className="space-y-2">
            {credentials.length === 0 ? (
              <p className="text-sm text-slate-400">No compliance credentials for this room yet.</p>
            ) : (
              credentials.map((credential) => (
                <div
                  key={credential.id}
                  className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-semibold text-primary-900 dark:text-white">{credential.requirementCode}</p>
                    <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                      {credential.status === "UNDER_REVIEW"
                        ? `Self-declared by Host — evidence: ${credential.evidenceRef || "none provided"}`
                        : credential.issuerSource || "No issuer recorded"}
                      {credential.expiresAt && ` — expires ${formatDate(credential.expiresAt)}`}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge tone={propertyComplianceCredentialStatusTone[credential.status] ?? "neutral"}>{credential.status.replace(/_/g, " ")}</Badge>
                    {credential.status === "UNDER_REVIEW" && (
                      <Button size="sm" variant="primary" loading={verifyingId === credential.id} onClick={() => handleVerifyDeclared(credential.id)}>
                        Verify
                      </Button>
                    )}
                    {(credential.status === "VALID" || credential.status === "EXPIRING") && (
                      <>
                        <Button size="sm" variant="outline" loading={suspendingId === credential.id} onClick={() => handleSuspend(credential.id)}>
                          Suspend
                        </Button>
                        <Button size="sm" variant="outline" loading={revokingId === credential.id} onClick={() => handleRevoke(credential.id)}>
                          Revoke
                        </Button>
                      </>
                    )}
                    {credential.status === "SUSPENDED" && (
                      <Button size="sm" variant="primary" loading={resumingId === credential.id} onClick={() => handleResume(credential.id)}>
                        Resume
                      </Button>
                    )}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </section>
  );
}
