"use client";

import { useCallback, useEffect, useState, type FormEvent } from "react";
import Link from "next/link";
import { BedDouble, Building2, MapPin, Pencil, Plus } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { Property, Room } from "@/lib/types";
import { Switch } from "@/components/ui/Switch";
import {
  createHostedProperty,
  createHostedRoom,
  errorMessage,
  listHostedProperties,
  listHostedRooms,
  updateHostedProperty,
  updateHostedRoom,
} from "@/lib/user-api";
import { ListARoomWizard } from "@/components/user/ListARoomWizard";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Card, EmptyState, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";

type PropertyForm = { id: number | null; address: string; city: string };
type RoomForm = { propertyId: number; id: number | null; size: string; hasEnsuite: boolean };

export function HostingPropertiesManager() {
  const { user } = useUserSession();
  const { toast, showToast } = useToast();
  const [properties, setProperties] = useState<Property[]>([]);
  const [roomsByProperty, setRoomsByProperty] = useState<Record<number, Room[]>>({});
  const [loading, setLoading] = useState(true);

  const [propertyForm, setPropertyForm] = useState<PropertyForm | null>(null);
  const [roomForm, setRoomForm] = useState<RoomForm | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    try {
      const owned = await listHostedProperties();
      setProperties(owned);
      const roomLists = await Promise.all(
        owned.map((property) => listHostedRooms(property.id).catch(() => [] as Room[]))
      );
      setRoomsByProperty(Object.fromEntries(owned.map((property, i) => [property.id, roomLists[i]])));
    } catch (err) {
      showToast(errorMessage(err, "Could not load your properties."), "error");
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    load();
  }, [load]);

  async function handlePropertySubmit(e: FormEvent) {
    e.preventDefault();
    if (!propertyForm) return;
    if (!propertyForm.address.trim() || !propertyForm.city.trim()) {
      setError("Both an address and a city are required.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const payload = { address: propertyForm.address.trim(), city: propertyForm.city.trim() };
      if (propertyForm.id === null) {
        await createHostedProperty(payload);
        showToast("Property added.");
      } else {
        await updateHostedProperty(propertyForm.id, payload);
        showToast("Property updated.");
      }
      setPropertyForm(null);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not save the property."));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleRoomSubmit(e: FormEvent) {
    e.preventDefault();
    if (!roomForm) return;
    const size = Number(roomForm.size || 0);
    if (!Number.isFinite(size) || size < 0) {
      setError("Room size must be a positive number of square feet.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const payload = { size: Math.round(size), hasEnsuite: roomForm.hasEnsuite };
      if (roomForm.id === null) {
        await createHostedRoom(roomForm.propertyId, payload);
        showToast("Room added.");
      } else {
        await updateHostedRoom(roomForm.propertyId, roomForm.id, payload);
        showToast("Room updated.");
      }
      setRoomForm(null);
      await load();
    } catch (err) {
      setError(errorMessage(err, "Could not save the room."));
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) return <Loader label="Loading your properties" />;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionHeading
          title="Your properties"
          subtitle="Use “List a Room” to add a room in one flow. The properties and rooms below are for editing what you already have."
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => setPropertyForm({ id: null, address: "", city: "" })}>
            <Plus className="h-4 w-4" /> Add Property
          </Button>
          <Button size="sm" onClick={() => setWizardOpen(true)}>
            <Plus className="h-4 w-4" /> List a Room
          </Button>
        </div>
      </div>

      {properties.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center gap-4 py-10 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
              <Building2 className="h-6 w-6" />
            </span>
            <EmptyState message="You are not hosting any rooms yet." />
            <Button size="sm" onClick={() => setWizardOpen(true)}>
              <Plus className="h-4 w-4" /> List a Room
            </Button>
          </div>
        </Card>
      ) : (
        <div className="space-y-4">
          {properties.map((property) => {
            const rooms = roomsByProperty[property.id] ?? [];
            return (
              <Card key={property.id}>
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
                      {property.address}
                    </p>
                    <p className="mt-0.5 flex items-center gap-1.5 text-xs text-slate-400">
                      <MapPin className="h-3.5 w-3.5" /> {property.city} · Property #{property.id}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge tone={property.status === "active" ? "success" : "neutral"}>{property.status}</Badge>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        setPropertyForm({ id: property.id, address: property.address, city: property.city })
                      }
                    >
                      <Pencil className="h-3.5 w-3.5" /> Edit
                    </Button>
                  </div>
                </div>

                <div className="mt-4 border-t border-slate-100 pt-4 dark:border-white/10">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-bold uppercase tracking-wide text-slate-400">
                      Rooms ({rooms.length})
                    </p>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() =>
                        setRoomForm({ propertyId: property.id, id: null, size: "", hasEnsuite: false })
                      }
                    >
                      <Plus className="h-3.5 w-3.5" /> Add room
                    </Button>
                  </div>

                  {rooms.length === 0 ? (
                    <p className="mt-3 text-xs text-slate-400">
                      No rooms yet. A listing must be linked to a room, so add one before you list.
                    </p>
                  ) : (
                    <ul className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                      {rooms.map((room) => (
                        <li
                          key={room.id}
                          className="flex items-center justify-between gap-3 rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60"
                        >
                          <div>
                            <p className="flex items-center gap-1.5 text-sm font-semibold text-slate-700 dark:text-slate-200">
                              <BedDouble className="h-4 w-4 text-primary-600" /> Room #{room.id}
                            </p>
                            <p className="mt-0.5 text-xs text-slate-400">
                              {room.size > 0 ? `${room.size} sq ft` : "Size not set"} ·{" "}
                              {room.hasEnsuite ? "En-suite" : "Shared bathroom"}
                            </p>
                          </div>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              setRoomForm({
                                propertyId: property.id,
                                id: room.id,
                                size: String(room.size),
                                hasEnsuite: room.hasEnsuite,
                              })
                            }
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </Card>
            );
          })}

          <p className="text-center text-xs text-slate-400">
            Manage your listings, photos and pricing from{" "}
            <Link href="/account/host/listings" className="font-semibold text-primary-700 dark:text-primary-300">
              My Listings
            </Link>
          </p>
        </div>
      )}

      <Modal
        open={Boolean(propertyForm)}
        onClose={() => setPropertyForm(null)}
        title={propertyForm?.id === null ? "Add a property" : "Edit property"}
      >
        <form onSubmit={handlePropertySubmit} className="space-y-4">
          <Field label="Address">
            <input
              value={propertyForm?.address ?? ""}
              onChange={(e) => setPropertyForm((f) => (f ? { ...f, address: e.target.value } : f))}
              placeholder="14 Linking Road, Bandra West"
              className={inputClass}
            />
          </Field>
          <Field label="City">
            <input
              value={propertyForm?.city ?? ""}
              onChange={(e) => setPropertyForm((f) => (f ? { ...f, city: e.target.value } : f))}
              placeholder="Mumbai"
              className={inputClass}
            />
          </Field>

          {error && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setPropertyForm(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              Save property
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        open={Boolean(roomForm)}
        onClose={() => setRoomForm(null)}
        title={roomForm?.id === null ? "Add a room" : "Edit room"}
      >
        <form onSubmit={handleRoomSubmit} className="space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
            Zoiko only hosts private rooms for 30+ night stays, so every room is created as a private room.
          </p>

          <Field label="Size (sq ft)">
            <input
              inputMode="numeric"
              value={roomForm?.size ?? ""}
              onChange={(e) => setRoomForm((f) => (f ? { ...f, size: e.target.value } : f))}
              placeholder="180"
              className={inputClass}
            />
          </Field>

          <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60">
            <span className="text-sm font-medium text-slate-600 dark:text-slate-300">Has a private en-suite</span>
            <Switch
              checked={roomForm?.hasEnsuite ?? false}
              onChange={(checked) => setRoomForm((f) => (f ? { ...f, hasEnsuite: checked } : f))}
            />
          </div>

          {error && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={() => setRoomForm(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              Save room
            </Button>
          </div>
        </form>
      </Modal>

      <ListARoomWizard
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        contact={{ name: user?.fullName ?? "", phone: user?.phone ?? "", email: user?.email ?? "" }}
        onCreated={async (submitted) => {
          setWizardOpen(false);
          await load();
          showToast(
            submitted
              ? "Listing submitted for review — a Zoiko admin will approve or reject it."
              : "Draft listing created — find it under My Listings when you're ready to submit it for review."
          );
        }}
      />

      <Toast toast={toast} />
    </div>
  );
}
