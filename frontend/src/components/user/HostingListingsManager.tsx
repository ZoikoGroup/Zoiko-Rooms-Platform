"use client";

import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { AlertTriangle, BedDouble, MapPin, Pencil, Plus, Send, XCircle } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Loader } from "@/components/ui/Loader";
import { Modal } from "@/components/ui/Modal";
import { HostedListing, Property, Room } from "@/lib/types";
import { listingStateLabel, listingStateTone } from "@/lib/status";
import { formatCurrency } from "@/lib/utils";
import {
  HostedListingInput,
  createHostedListing,
  errorMessage,
  listHostedListings,
  listHostedProperties,
  listHostedRooms,
  submitHostedListingForReview,
  updateHostedListing,
} from "@/lib/user-api";
import { IdentityGate } from "@/components/user/IdentityGate";
import { ListARoomWizard } from "@/components/user/ListARoomWizard";
import { useUserSession } from "@/components/user/UserSessionContext";
import { Card, EmptyState, Field, SectionHeading, Toast, inputClass, useToast } from "@/components/user/ui";
import { ImageGalleryUploader } from "@/components/admin/ImageGalleryUploader";

const MAX_LISTING_IMAGES = 10;

// Mirrors backend SUPPORTED_CURRENCIES (app/models/listing.py) -- kept in sync by hand
// since currency validation happens server-side and this is just the picker.
const SUPPORTED_CURRENCIES = ["INR", "GBP", "USD", "EUR", "CAD", "AUD", "AED", "SGD", "NZD"];

interface ListingFormState {
  id: string | null;
  name: string;
  roomId: string;
  roomType: string;
  city: string;
  location: string;
  pricePerNight: string;
  currency: string;
  guests: string;
  bedrooms: string;
  bathrooms: string;
  size: string;
  minStayNights: string;
  description: string;
  amenities: string;
  images: string[];
  contactName: string;
  contactPhone: string;
  contactEmail: string;
}

function toFormState(listing: HostedListing): ListingFormState {
  return {
    id: listing.id,
    name: listing.name,
    roomId: listing.roomId === null ? "" : String(listing.roomId),
    roomType: listing.roomType,
    city: listing.city,
    location: listing.location,
    pricePerNight: String(listing.pricePerNight),
    currency: listing.currency,
    guests: String(listing.guests),
    bedrooms: String(listing.bedrooms),
    bathrooms: String(listing.bathrooms),
    size: String(listing.size),
    minStayNights: String(listing.minStayNights),
    description: listing.description,
    amenities: listing.amenities.join(", "),
    images: listing.images,
    contactName: listing.contactName,
    contactPhone: listing.contactPhone,
    contactEmail: listing.contactEmail,
  };
}

function splitList(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

export function HostingListingsManager() {
  const { user } = useUserSession();
  const { toast, showToast } = useToast();

  const [listings, setListings] = useState<HostedListing[]>([]);
  const [properties, setProperties] = useState<Property[]>([]);
  const [roomsByProperty, setRoomsByProperty] = useState<Record<number, Room[]>>({});
  const [loading, setLoading] = useState(true);

  const [form, setForm] = useState<ListingFormState | null>(null);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const [busyListingId, setBusyListingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      const [owned, mine] = await Promise.all([listHostedProperties(), listHostedListings()]);
      setProperties(owned);
      const roomLists = await Promise.all(
        owned.map((property) => listHostedRooms(property.id).catch(() => [] as Room[]))
      );
      setRoomsByProperty(Object.fromEntries(owned.map((property, i) => [property.id, roomLists[i]])));
      setListings(mine);
    } catch (err) {
      showToast(errorMessage(err, "Could not load your listings."), "error");
    } finally {
      setLoading(false);
    }
  }, [user, showToast]);

  useEffect(() => {
    load();
  }, [load]);

  const roomOptions = useMemo(
    () =>
      properties.flatMap((property) =>
        (roomsByProperty[property.id] ?? []).map((room) => ({
          id: room.id,
          label: `Room #${room.id} — ${property.address}, ${property.city}`,
        }))
      ),
    [properties, roomsByProperty]
  );

  function openEdit(listing: HostedListing) {
    setError("");
    setForm(toFormState(listing));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!form || !user) return;

    const roomId = Number(form.roomId);
    if (!Number.isInteger(roomId) || roomId <= 0) {
      setError("Pick one of your rooms — a hosted listing must be linked to a room you own.");
      return;
    }
    if (!form.name.trim() || !form.city.trim() || !form.location.trim()) {
      setError("Name, city and area are all required.");
      return;
    }
    const minStay = Number(form.minStayNights);
    if (!Number.isFinite(minStay) || minStay < 30) {
      setError("Zoiko is a long-stay marketplace — the minimum stay must be at least 30 nights.");
      return;
    }
    const price = Number(form.pricePerNight);
    if (!Number.isFinite(price) || price <= 0) {
      setError("Enter a nightly price greater than zero.");
      return;
    }

    const payload: HostedListingInput = {
      name: form.name.trim(),
      roomType: form.roomType.trim() || "Private room",
      city: form.city.trim(),
      location: form.location.trim(),
      pricePerNight: price,
      currency: form.currency,
      guests: Math.max(1, Number(form.guests) || 1),
      bedrooms: Number(form.bedrooms) || 0,
      bathrooms: Number(form.bathrooms) || 1,
      size: Number(form.size) || 0,
      description: form.description.trim(),
      amenities: splitList(form.amenities),
      images: form.images,
      minStayNights: Math.round(minStay),
      roomId,
      contactName: form.contactName.trim(),
      contactPhone: form.contactPhone.trim(),
      contactEmail: form.contactEmail.trim(),
    };

    setError("");
    setSubmitting(true);
    try {
      const saved =
        form.id === null ? await createHostedListing(payload) : await updateHostedListing(form.id, payload);
      setListings((prev) => [saved, ...prev.filter((l) => l.id !== saved.id)]);
      setForm(null);
      showToast(form.id === null ? "Draft listing created." : "Listing updated.");
    } catch (err) {
      setError(errorMessage(err, "Could not save the listing."));
    } finally {
      setSubmitting(false);
    }
  }

  async function submitForReview(listingId: string) {
    if (!user) return;
    setBusyListingId(listingId);
    try {
      const submitted = await submitHostedListingForReview(listingId);
      setListings((prev) => prev.map((l) => (l.id === submitted.id ? submitted : l)));
      showToast("Submitted for review — a Zoiko admin will approve or reject it.");
    } catch (err) {
      showToast(errorMessage(err, "Could not submit this listing for review."), "error");
    } finally {
      setBusyListingId(null);
    }
  }

  if (loading) return <Loader label="Loading your listings" />;

  return (
    <div className="space-y-5">
      <IdentityGate action="submit a listing for review">
        <Card className="!bg-emerald-50 !ring-emerald-200 dark:!bg-emerald-500/10 dark:!ring-emerald-500/20">
          <p className="text-sm font-semibold text-emerald-800 dark:text-emerald-300">
            Your identity is verified.
          </p>
        </Card>
      </IdentityGate>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <SectionHeading
          title="Your listings"
          subtitle="Use “List a Room” to create a new listing. Submit it for review when it's ready, then it publishes once a Zoiko admin approves it."
        />
        <Button size="sm" onClick={() => setWizardOpen(true)}>
          <Plus className="h-4 w-4" /> List a Room
        </Button>
      </div>

      {listings.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center gap-4 py-10 text-center">
            <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary-50 text-primary-700 dark:bg-primary-500/10 dark:text-primary-300">
              <BedDouble className="h-6 w-6" />
            </span>
            <EmptyState message="You have not listed any rooms yet." />
            <Button size="sm" onClick={() => setWizardOpen(true)}>
              <Plus className="h-4 w-4" /> List a Room
            </Button>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {listings.map((listing) => {
            const busy = busyListingId === listing.id;
            return (
              <Card key={listing.id}>
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
                        {listing.name}
                      </p>
                      <Badge tone={listingStateTone[listing.state] ?? "neutral"}>
                        {listingStateLabel[listing.state] ?? listing.state}
                      </Badge>
                    </div>
                    <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-400">
                      <span className="flex items-center gap-1">
                        <MapPin className="h-3 w-3" /> {listing.location}, {listing.city}
                      </span>
                      <span>{formatCurrency(listing.pricePerNight, listing.currency)} / night</span>
                      <span>Min. {listing.minStayNights} nights</span>
                      {listing.roomId !== null && <span>Room #{listing.roomId}</span>}
                      <span className="text-slate-300 dark:text-slate-600">{listing.id}</span>
                    </p>
                  </div>

                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" variant="ghost" onClick={() => openEdit(listing)}>
                      <Pencil className="h-3.5 w-3.5" /> Edit
                    </Button>
                    {(listing.state === "DRAFT" || listing.state === "REJECTED") && (
                      <Button size="sm" loading={busy} onClick={() => submitForReview(listing.id)}>
                        <Send className="h-3.5 w-3.5" /> Submit for Review
                      </Button>
                    )}
                  </div>
                </div>

                {listing.state === "REVIEW" && (
                  <div className="mt-4 rounded-xl bg-primary-50 px-4 py-3 text-xs text-primary-700 ring-1 ring-primary-200 dark:bg-primary-500/10 dark:text-primary-300 dark:ring-primary-500/20">
                    <p className="flex items-center gap-1.5 font-semibold">
                      <AlertTriangle className="h-3.5 w-3.5" /> Awaiting review by a Zoiko admin.
                    </p>
                  </div>
                )}

                {listing.state === "REJECTED" && (
                  <div className="mt-4 rounded-xl bg-accent-50 px-4 py-3 text-xs text-accent-700 ring-1 ring-accent-200 dark:bg-accent-500/10 dark:text-accent-300 dark:ring-accent-500/20">
                    <p className="flex items-center gap-1.5 font-semibold">
                      <XCircle className="h-3.5 w-3.5" /> Not approved
                    </p>
                    {listing.rejectionReason && <p className="mt-1">Reason: {listing.rejectionReason}</p>}
                    <p className="mt-1">Make the necessary changes and submit it for review again.</p>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}

      <Modal
        open={Boolean(form)}
        onClose={() => setForm(null)}
        title={form?.id === null ? "Create a listing" : "Edit listing"}
      >
        <form onSubmit={handleSubmit} className="max-h-[65vh] space-y-4 overflow-y-auto pr-1">
          <Field label="Room" hint="A listing is always linked to one room you own.">
            <select
              value={form?.roomId ?? ""}
              onChange={(e) => setForm((f) => (f ? { ...f, roomId: e.target.value } : f))}
              className={inputClass}
            >
              <option value="">Select a room...</option>
              {roomOptions.map((room) => (
                <option key={room.id} value={room.id}>
                  {room.label}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Listing name">
            <input
              value={form?.name ?? ""}
              onChange={(e) => setForm((f) => (f ? { ...f, name: e.target.value } : f))}
              placeholder="Sunlit private room in Bandra West"
              className={inputClass}
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Field label="City">
              <input
                value={form?.city ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, city: e.target.value } : f))}
                placeholder="Mumbai"
                className={inputClass}
              />
            </Field>
            <Field label="Area / neighbourhood">
              <input
                value={form?.location ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, location: e.target.value } : f))}
                placeholder="Bandra West"
                className={inputClass}
              />
            </Field>
            <Field label="Room type">
              <input
                value={form?.roomType ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, roomType: e.target.value } : f))}
                placeholder="Private room"
                className={inputClass}
              />
            </Field>
            <Field label="Price per night">
              <input
                inputMode="decimal"
                value={form?.pricePerNight ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, pricePerNight: e.target.value } : f))}
                placeholder="1800"
                className={inputClass}
              />
            </Field>
            <Field label="Currency">
              <select
                value={form?.currency ?? "INR"}
                onChange={(e) => setForm((f) => (f ? { ...f, currency: e.target.value } : f))}
                className={inputClass}
              >
                {SUPPORTED_CURRENCIES.map((code) => (
                  <option key={code} value={code}>
                    {code}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Minimum stay (nights)" hint="30 or more.">
              <input
                inputMode="numeric"
                value={form?.minStayNights ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, minStayNights: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Guests">
              <input
                inputMode="numeric"
                value={form?.guests ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, guests: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Bedrooms">
              <input
                inputMode="numeric"
                value={form?.bedrooms ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, bedrooms: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Bathrooms">
              <input
                inputMode="numeric"
                value={form?.bathrooms ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, bathrooms: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Size (sq ft)">
              <input
                inputMode="numeric"
                value={form?.size ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, size: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
          </div>

          <Field label="Description">
            <textarea
              value={form?.description ?? ""}
              onChange={(e) => setForm((f) => (f ? { ...f, description: e.target.value } : f))}
              rows={3}
              placeholder="Quiet furnished room with a study desk, 10 minutes from the station..."
              className={inputClass}
            />
          </Field>

          <Field label="Amenities" hint="Comma separated, e.g. Wi-Fi, Washing machine, Air conditioning">
            <input
              value={form?.amenities ?? ""}
              onChange={(e) => setForm((f) => (f ? { ...f, amenities: e.target.value } : f))}
              className={inputClass}
            />
          </Field>

          <Field label="Room photos" hint="Upload photos of the room. The first photo is used as the cover image.">
            <ImageGalleryUploader
              images={form?.images ?? []}
              onChange={(images) => setForm((f) => (f ? { ...f, images } : f))}
              uploadUrl="/api/users/hosting/uploads/images"
              maxImages={MAX_LISTING_IMAGES}
            />
          </Field>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <Field label="Contact name">
              <input
                value={form?.contactName ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, contactName: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Contact phone">
              <input
                value={form?.contactPhone ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, contactPhone: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
            <Field label="Contact email">
              <input
                value={form?.contactEmail ?? ""}
                onChange={(e) => setForm((f) => (f ? { ...f, contactEmail: e.target.value } : f))}
                className={inputClass}
              />
            </Field>
          </div>

          {error && (
            <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
              {error}
            </p>
          )}

          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" onClick={() => setForm(null)}>
              Cancel
            </Button>
            <Button type="submit" loading={submitting}>
              {form?.id === null ? "Create draft" : "Save changes"}
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
              : "Draft listing created."
          );
        }}
      />

      <Toast toast={toast} />
    </div>
  );
}
