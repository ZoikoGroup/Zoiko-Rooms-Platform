"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, ClipboardCheck, Landmark, Plus, ShieldCheck, ShieldX, XCircle } from "lucide-react";
import { AuthorityRecord, MarketRelease, OccupancyClassification, OccupancyReviewState, Property, Room } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { apiClientFetch } from "@/lib/api-client";
import { formatDate } from "@/lib/utils";
import {
  authorityRecordStatusLabel,
  authorityRecordStatusTone,
  formatClassificationLabel,
  marketReleaseStatusLabel,
  marketReleaseStatusTone,
  occupancyReviewStateLabel,
  occupancyReviewStateTone,
} from "@/lib/status";

type RoomOption = Room & { property: Property };

const emptyReleaseForm = { jurisdiction: "IN", minStayNights: "30" };
const emptyClassifyForm = { classification: "shared_residential_room", confidence: "1", evidenceRef: "", reviewState: "APPROVED" as OccupancyReviewState };
const emptyAuthorityForm = { roomId: "", authorityType: "lease_agreement", evidenceRef: "" };

export function TrustSafetyManager() {
  const [releases, setReleases] = useState<MarketRelease[]>([]);
  const [authorityRecords, setAuthorityRecords] = useState<AuthorityRecord[]>([]);
  const [rooms, setRooms] = useState<RoomOption[]>([]);
  const [classifications, setClassifications] = useState<Record<number, OccupancyClassification | null>>({});
  const [toast, setToast] = useState("");
  const [releaseModalOpen, setReleaseModalOpen] = useState(false);
  const [releaseForm, setReleaseForm] = useState(emptyReleaseForm);
  const [classifyRoomId, setClassifyRoomId] = useState<number | null>(null);
  const [classifyForm, setClassifyForm] = useState(emptyClassifyForm);
  const [authorityModalOpen, setAuthorityModalOpen] = useState(false);
  const [authorityForm, setAuthorityForm] = useState(emptyAuthorityForm);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  useEffect(() => {
    async function loadAll() {
      try {
        const [releasesData, authorityData, properties, allRooms] = await Promise.all([
          apiClientFetch<MarketRelease[]>("/api/market-releases"),
          apiClientFetch<AuthorityRecord[]>("/api/authority-records"),
          apiClientFetch<Property[]>("/api/properties"),
          apiClientFetch<Room[]>("/api/properties/rooms"),
        ]);
        setReleases(releasesData);
        setAuthorityRecords(authorityData);

        const propertyById = new Map(properties.map((p) => [p.id, p]));
        const roomOptions = allRooms.flatMap((r) => {
          const property = propertyById.get(r.propertyId);
          return property ? [{ ...r, property }] : [];
        });
        setRooms(roomOptions);

        const classificationList = roomOptions.length
          ? await apiClientFetch<OccupancyClassification[]>(
              `/api/rooms/occupancy-classifications?room_ids=${roomOptions.map((r) => r.id).join(",")}`
            )
          : [];
        const classificationByRoomId = new Map(classificationList.map((c) => [c.roomId, c]));
        setClassifications(Object.fromEntries(roomOptions.map((r) => [r.id, classificationByRoomId.get(r.id) ?? null])));
      } catch {
        showToast("Failed to load trust & safety data");
      }
    }
    loadAll();
  }, []);

  async function createRelease(e: React.FormEvent) {
    e.preventDefault();
    if (!releaseForm.jurisdiction.trim()) return;
    try {
      const created = await apiClientFetch<MarketRelease>("/api/market-releases", {
        method: "POST",
        body: JSON.stringify({
          jurisdiction: releaseForm.jurisdiction.trim(),
          minStayNights: Number(releaseForm.minStayNights) || 30,
        }),
      });
      setReleases((prev) => [...prev, created]);
      setReleaseModalOpen(false);
      setReleaseForm(emptyReleaseForm);
      showToast("Market release created as draft");
    } catch {
      showToast("Failed to create market release");
    }
  }

  async function setReleaseStatus(id: number, action: "approve" | "disable") {
    try {
      const updated = await apiClientFetch<MarketRelease>(`/api/market-releases/${id}/${action}`, { method: "POST" });
      setReleases((prev) => prev.map((r) => (r.id === id ? updated : r)));
      showToast(action === "approve" ? "Market release activated" : "Market release disabled");
    } catch {
      showToast(`Failed to ${action} market release`);
    }
  }

  async function submitAuthorityRecord(e: React.FormEvent) {
    e.preventDefault();
    if (!authorityForm.roomId || !authorityForm.authorityType.trim()) return;
    try {
      const created = await apiClientFetch<AuthorityRecord>("/api/authority-records", {
        method: "POST",
        body: JSON.stringify({
          roomId: Number(authorityForm.roomId),
          authorityType: authorityForm.authorityType.trim(),
          evidenceRef: authorityForm.evidenceRef.trim(),
        }),
      });
      setAuthorityRecords((prev) => [...prev, created]);
      setAuthorityModalOpen(false);
      setAuthorityForm(emptyAuthorityForm);
      showToast("Authority record submitted — verify it below to unblock this room");
    } catch {
      showToast("Failed to submit authority record");
    }
  }

  async function verifyAuthority(id: number) {
    try {
      const updated = await apiClientFetch<AuthorityRecord>(`/api/authority-records/${id}/verify`, { method: "POST" });
      setAuthorityRecords((prev) => prev.map((r) => (r.id === id ? updated : r)));
      showToast("Authority record verified");
    } catch {
      showToast("Failed to verify authority record");
    }
  }

  async function rejectAuthority(id: number) {
    try {
      const updated = await apiClientFetch<AuthorityRecord>(`/api/authority-records/${id}/reject`, { method: "POST" });
      setAuthorityRecords((prev) => prev.map((r) => (r.id === id ? updated : r)));
      showToast("Authority record rejected");
    } catch {
      showToast("Failed to reject authority record");
    }
  }

  function openClassifyModal(room: RoomOption) {
    const existing = classifications[room.id];
    setClassifyForm(
      existing
        ? {
            classification: existing.classification,
            confidence: String(existing.confidence),
            evidenceRef: existing.evidenceRef,
            reviewState: existing.reviewState,
          }
        : emptyClassifyForm
    );
    setClassifyRoomId(room.id);
  }

  async function submitClassification(e: React.FormEvent) {
    e.preventDefault();
    if (classifyRoomId === null) return;
    try {
      const updated = await apiClientFetch<OccupancyClassification>(`/api/rooms/${classifyRoomId}/occupancy-classification`, {
        method: "PUT",
        body: JSON.stringify({
          classification: classifyForm.classification.trim(),
          confidence: Number(classifyForm.confidence) || 0,
          evidenceRef: classifyForm.evidenceRef.trim(),
          reviewState: classifyForm.reviewState,
        }),
      });
      setClassifications((prev) => ({ ...prev, [classifyRoomId]: updated }));
      setClassifyRoomId(null);
      showToast("Occupancy classification saved");
    } catch {
      showToast("Failed to save occupancy classification");
    }
  }

  return (
    <div className="space-y-6">
      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Landmark className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Market Releases</h2>
          </div>
          <Button size="sm" variant="accent" onClick={() => setReleaseModalOpen(true)}>
            <Plus className="h-3.5 w-3.5" /> Configure Market Release
          </Button>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          A Market Release controls whether a jurisdiction is enabled for listing publication. This is an
          informational compliance signal for admin review — it does not automatically block publishing a listing.
        </p>
        <div className="mt-4 space-y-2">
          {releases.map((release) => (
            <div
              key={release.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">{release.jurisdiction}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">Min stay: {release.minStayNights} nights</p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={marketReleaseStatusTone[release.status]}>{marketReleaseStatusLabel[release.status]}</Badge>
                {release.status !== "active" && (
                  <Button size="sm" variant="primary" onClick={() => setReleaseStatus(release.id, "approve")}>
                    Activate
                  </Button>
                )}
                {release.status === "active" && (
                  <Button size="sm" variant="outline" onClick={() => setReleaseStatus(release.id, "disable")}>
                    Disable
                  </Button>
                )}
              </div>
            </div>
          ))}
          {releases.length === 0 && <p className="text-sm text-slate-400 dark:text-slate-400">No market releases yet.</p>}
        </div>
      </section>

      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <ShieldCheck className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
            <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Authority Records</h2>
          </div>
          <Button size="sm" variant="accent" onClick={() => setAuthorityModalOpen(true)}>
            <Plus className="h-3.5 w-3.5" /> Add Authority Record
          </Button>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          An Authority Record is evidence that a host has the right to offer a room (e.g. a lease agreement,
          ownership deed, or NOC). Verifying it here is an informational compliance signal for admin review — it
          does not automatically block publishing a listing.
        </p>
        <div className="mt-4 space-y-2">
          {authorityRecords.map((record) => {
            const room = rooms.find((r) => r.id === record.roomId);
            return (
            <div
              key={record.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
            >
              <div>
                <p className="text-sm font-semibold text-primary-900 dark:text-white">
                  {room ? room.property.address : `Room #${record.roomId}`} — {record.authorityType}
                </p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Evidence: {record.evidenceRef || "—"}
                  {record.expiresAt && ` · expires ${formatDate(record.expiresAt)}`}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={authorityRecordStatusTone[record.status]}>{authorityRecordStatusLabel[record.status]}</Badge>
                {record.status === "pending" && (
                  <>
                    <Button size="sm" variant="primary" onClick={() => verifyAuthority(record.id)}>
                      <CheckCircle2 className="h-3.5 w-3.5" /> Verify
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => rejectAuthority(record.id)}>
                      <XCircle className="h-3.5 w-3.5" /> Reject
                    </Button>
                  </>
                )}
              </div>
            </div>
            );
          })}
          {authorityRecords.length === 0 && (
            <p className="text-sm text-slate-400 dark:text-slate-400">No authority records submitted yet.</p>
          )}
        </div>
      </section>

      <section className="rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="flex items-center gap-2">
          <ClipboardCheck className="h-4.5 w-4.5 text-primary-700 dark:text-primary-300" />
          <h2 className="font-heading text-base font-bold text-primary-900 dark:text-white">Occupancy Classification</h2>
        </div>
        <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
          Occupancy Classification records what type of accommodation a room represents (e.g. a shared residential
          room). This is an informational compliance signal for admin review — it does not automatically block
          publishing a listing.
        </p>
        <div className="mt-4 space-y-2">
          {rooms.map((room) => {
            const classification = classifications[room.id];
            const reviewState = classification?.reviewState ?? "UNKNOWN";
            return (
              <div
                key={room.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
              >
                <div>
                  <p className="text-sm font-semibold text-primary-900 dark:text-white">
                    Room #{room.id} — {room.property.address}
                    {room.size > 0 ? ` (${room.size} sq ft${room.hasEnsuite ? ", en-suite" : ""})` : ""}
                  </p>
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {formatClassificationLabel(classification?.classification ?? "")}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Badge tone={occupancyReviewStateTone[reviewState]}>{occupancyReviewStateLabel[reviewState]}</Badge>
                  <Button size="sm" variant="outline" onClick={() => openClassifyModal(room)}>
                    <ShieldX className="h-3.5 w-3.5" /> {classification ? "Change Classification" : "Set Classification"}
                  </Button>
                </div>
              </div>
            );
          })}
          {rooms.length === 0 && <p className="text-sm text-slate-400 dark:text-slate-400">No rooms yet.</p>}
        </div>
      </section>

      <Modal open={releaseModalOpen} onClose={() => setReleaseModalOpen(false)} title="Configure Market Release">
        <form onSubmit={createRelease} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Jurisdiction
            </label>
            <input
              value={releaseForm.jurisdiction}
              onChange={(e) => setReleaseForm((f) => ({ ...f, jurisdiction: e.target.value }))}
              placeholder="IN"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Minimum Stay (nights)
            </label>
            <input
              type="number"
              min={30}
              value={releaseForm.minStayNights}
              onChange={(e) => setReleaseForm((f) => ({ ...f, minStayNights: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
          </div>
          <Button type="submit" variant="primary" fullWidth>
            Create Release
          </Button>
        </form>
      </Modal>

      <Modal open={authorityModalOpen} onClose={() => setAuthorityModalOpen(false)} title="Add Authority Record">
        <form onSubmit={submitAuthorityRecord} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Room
            </label>
            <select
              value={authorityForm.roomId}
              onChange={(e) => setAuthorityForm((f) => ({ ...f, roomId: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            >
              <option value="">Select a room…</option>
              {rooms.map((room) => (
                <option key={room.id} value={room.id}>
                  Room #{room.id} — {room.property.address}
                  {room.size > 0 ? ` (${room.size} sq ft${room.hasEnsuite ? ", en-suite" : ""})` : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Authority Type
            </label>
            <input
              value={authorityForm.authorityType}
              onChange={(e) => setAuthorityForm((f) => ({ ...f, authorityType: e.target.value }))}
              placeholder="lease_agreement, ownership_deed, noc, etc."
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Evidence Reference
            </label>
            <input
              value={authorityForm.evidenceRef}
              onChange={(e) => setAuthorityForm((f) => ({ ...f, evidenceRef: e.target.value }))}
              placeholder="Document ID, uploaded file ref, etc."
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <Button type="submit" variant="primary" fullWidth>
            Submit for Review
          </Button>
        </form>
      </Modal>

      <Modal open={classifyRoomId !== null} onClose={() => setClassifyRoomId(null)} title="Set Occupancy Classification">
        <form onSubmit={submitClassification} className="space-y-3.5">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Classification
            </label>
            <input
              value={classifyForm.classification}
              onChange={(e) => setClassifyForm((f) => ({ ...f, classification: e.target.value }))}
              placeholder="shared_residential_room"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Review State
            </label>
            <select
              value={classifyForm.reviewState}
              onChange={(e) => setClassifyForm((f) => ({ ...f, reviewState: e.target.value as OccupancyReviewState }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="UNKNOWN">Not yet classified</option>
              <option value="UNSUPPORTED">Classification unresolved</option>
              <option value="APPROVED">Approved</option>
            </select>
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Confidence (0–1)
            </label>
            <input
              type="number"
              min={0}
              max={1}
              step={0.05}
              value={classifyForm.confidence}
              onChange={(e) => setClassifyForm((f) => ({ ...f, confidence: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Evidence Reference
            </label>
            <input
              value={classifyForm.evidenceRef}
              onChange={(e) => setClassifyForm((f) => ({ ...f, evidenceRef: e.target.value }))}
              placeholder="Zoning letter ref, inspection ID, etc."
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <Button type="submit" variant="primary" fullWidth>
            Save Classification
          </Button>
        </form>
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
