"use client";

import { useCallback, useMemo, useState } from "react";
import { RentalChatThread } from "@/components/user/RentalChatThread";
import { SubletRequest, UserOccupancy } from "@/lib/types";
import {
  listRenterApplicationMessages,
  listSubletMessages,
  sendRenterApplicationMessage,
  sendSubletMessage,
} from "@/lib/user-api";

interface Thread {
  key: string;
  label: string;
  placeholder: string;
  emptyText: string;
  load: () => ReturnType<typeof listSubletMessages>;
  send: (body: string) => ReturnType<typeof sendSubletMessage>;
}

/** My Rentals -> Messages: the host thread for this tenancy, plus -- on a
 *  sublet -- the renter it was sublet from, or the subtenants this renter
 *  named. A subtenant never applied for the room, so the host thread is keyed
 *  on the tenancy's own application rather than an Applications-page entry. */
export function RentalMessagesTab({
  occupancy,
  subletRequests,
}: {
  occupancy: UserOccupancy;
  subletRequests: SubletRequest[];
}) {
  const threads = useMemo<Thread[]>(() => {
    const out: Thread[] = [];
    const applicationId = occupancy.applicationId;
    if (applicationId) {
      out.push({
        key: "host",
        label: occupancy.hostName ? `Host · ${occupancy.hostName}` : "Host / Manager",
        placeholder: "Message the host or authorized manager…",
        emptyText: "No messages yet. Ask the host anything about this rental.",
        load: () => listRenterApplicationMessages(applicationId),
        send: (body) => sendRenterApplicationMessage(applicationId, body),
      });
    }
    const subletFromId = occupancy.subletFromRequestId;
    if (subletFromId) {
      const name = occupancy.subletFromRenterName || "the renter";
      out.push({
        key: `sublet-${subletFromId}`,
        label: `${occupancy.subletFromRenterName || "Renter"} · you sublet from`,
        placeholder: `Message ${name}…`,
        emptyText: `No messages yet. You can reach ${name}, who sublet this room to you, here.`,
        load: () => listSubletMessages(subletFromId),
        send: (body) => sendSubletMessage(subletFromId, body),
      });
    }
    for (const request of subletRequests) {
      if (request.currentOccupancyId !== occupancy.id || request.status === "draft" || !request.proposedRenterPartyId) continue;
      const name = request.proposedRenterName || "your subtenant";
      out.push({
        key: `sublet-${request.id}`,
        label: `${request.proposedRenterName || "Subtenant"} · your sublet`,
        placeholder: `Message ${name}…`,
        emptyText: `No messages yet. You can reach ${name} about this sublet here.`,
        load: () => listSubletMessages(request.id),
        send: (body) => sendSubletMessage(request.id, body),
      });
    }
    return out;
  }, [occupancy, subletRequests]);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = threads.find((t) => t.key === selectedKey) ?? threads[0];

  if (!selected) {
    return <p className="text-xs text-[#5B6886]">Messaging isn&apos;t available for this rental.</p>;
  }

  return (
    <div className="space-y-4">
      {threads.length > 1 && (
        <div className="flex flex-wrap gap-2" role="tablist" aria-label="Conversations">
          {threads.map((t) => (
            <button
              key={t.key}
              type="button"
              role="tab"
              aria-selected={t.key === selected.key}
              onClick={() => setSelectedKey(t.key)}
              className={`rounded-full border px-3 py-1.5 text-xs font-bold transition-colors ${
                t.key === selected.key
                  ? "border-[#0E2F73] bg-[#0E2F73] text-white"
                  : "border-[#E1E7F1] bg-white text-[#4A5878] hover:text-[#0E2F73]"
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}
      <RentalChatThread
        key={selected.key}
        load={selected.load}
        send={selected.send}
        placeholder={selected.placeholder}
        emptyText={selected.emptyText}
      />
    </div>
  );
}

/** The renter <-> subtenant thread on one sublet request (Sublets page). */
export function SubletChatThread({ subletRequestId, counterpartName }: { subletRequestId: number; counterpartName: string }) {
  const load = useCallback(() => listSubletMessages(subletRequestId), [subletRequestId]);
  const send = useCallback((body: string) => sendSubletMessage(subletRequestId, body), [subletRequestId]);
  return (
    <RentalChatThread
      load={load}
      send={send}
      placeholder={`Message ${counterpartName}…`}
      emptyText={`No messages yet. You can reach ${counterpartName} about this sublet here.`}
    />
  );
}
