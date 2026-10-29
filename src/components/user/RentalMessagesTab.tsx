"use client";

import { useCallback, useMemo, useState } from "react";
import { RentalChatThread } from "@/components/user/RentalChatThread";
import { SubletRequest, UserOccupancy } from "@/lib/types";
import {
  listHostedSubletMessages,
  listRenterApplicationMessages,
  listSubletMessages,
  sendHostedSubletMessage,
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

/** My Rentals -> Messages: the private thread with the host for this tenancy. The renter and the person they
 *  sublet to each talk to the host only -- never to each other. A subtenant never applied for the room, so the
 *  host thread is keyed on the tenancy's own application. */
export function RentalMessagesTab({
  occupancy,
}: {
  occupancy: UserOccupancy;
  subletRequests?: SubletRequest[];
}) {
  const threads = useMemo<Thread[]>(() => {
    const out: Thread[] = [];
    const applicationId = occupancy.applicationId;
    const subletFromId = occupancy.subletFromRequestId;
    if (occupancy.subletFromHostThread && subletFromId) {
      // Took the room over through a sublet: the host thread is kept with that sublet, not the renter's application.
      out.push({
        key: "host",
        label: occupancy.hostName ? `Host · ${occupancy.hostName}` : "Host / Manager",
        placeholder: "Message the host or authorized manager…",
        emptyText: "No messages yet. Ask the host anything about this rental.",
        load: () => listSubletMessages(subletFromId, "host"),
        send: (body) => sendSubletMessage(subletFromId, body, "host"),
      });
    } else if (applicationId) {
      out.push({
        key: "host",
        label: occupancy.hostName ? `Host · ${occupancy.hostName}` : "Host / Manager",
        placeholder: "Message the host or authorized manager…",
        emptyText: "No messages yet. Ask the host anything about this rental.",
        load: () => listRenterApplicationMessages(applicationId),
        send: (body) => sendRenterApplicationMessage(applicationId, body),
      });
    }
    return out;
  }, [occupancy]);

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

/** The private thread between the host and the person added to a room (a co-tenant or permitted occupant),
 *  from either side. The renter who asked can't see it. */
export function SubletHostChatThread({
  subletRequestId,
  side,
  counterpartName,
}: {
  subletRequestId: number;
  side: "host" | "occupant";
  counterpartName: string;
}) {
  const load = useCallback(
    () => (side === "host" ? listHostedSubletMessages(subletRequestId) : listSubletMessages(subletRequestId, "host")),
    [subletRequestId, side],
  );
  const send = useCallback(
    (body: string) => (side === "host" ? sendHostedSubletMessage(subletRequestId, body) : sendSubletMessage(subletRequestId, body, "host")),
    [subletRequestId, side],
  );
  return (
    <RentalChatThread
      load={load}
      send={send}
      placeholder={`Message ${counterpartName}…`}
      emptyText={
        side === "host"
          ? `No messages yet. This is a private thread between you and ${counterpartName}.`
          : `No messages yet. Ask ${counterpartName} anything about sharing this room.`
      }
    />
  );
}
