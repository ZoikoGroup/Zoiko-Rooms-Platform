"use client";

import { useEffect, useState } from "react";
import { Building2, Check, ChevronLeft, ChevronRight, FileEdit, Send } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { Switch } from "@/components/ui/Switch";
import { Property, Room } from "@/lib/types";
import {
  HostedListingInput,
  createHostedListing,
  createHostedProperty,
  createHostedRoom,
  errorMessage,
  listHostedProperties,
  listHostedRooms,
  submitHostedListingForReview,
} from "@/lib/user-api";
import { ImageGalleryUploader } from "@/components/admin/ImageGalleryUploader";
import { formatCurrency } from "@/lib/utils";
import { Field, inputClass } from "@/components/user/ui";

const MAX_LISTING_IMAGES = 10;
const SUPPORTED_CURRENCIES = ["INR", "GBP", "USD", "EUR", "CAD", "AUD", "AED", "SGD", "NZD"];
const STEPS = ["Property", "Room", "Listing", "Photos", "Review"] as const;

type PropertyChoice = { mode: "existing"; propertyId: number } | { mode: "new"; address: string; city: string };
type RoomChoice = { mode: "existing"; roomId: number } | { mode: "new"; size: string; hasEnsuite: boolean };

interface ListingDetailsForm {
  name: string;
  roomType: string;
  location: string;
  pricePerNight: string;
  currency: string;
  minStayNights: string;
  guests: string;
  bedrooms: string;
  bathrooms: string;
  size: string;
  description: string;
  amenities: string;
  images: string[];
  contactName: string;
  contactPhone: string;
  contactEmail: string;
}

function emptyDetails(contact: { name: string; phone: string; email: string }): ListingDetailsForm {
  return {
    name: "",
    roomType: "Private room",
    location: "",
    pricePerNight: "",
    currency: "INR",
    minStayNights: "30",
    guests: "1",
    bedrooms: "1",
    bathrooms: "1",
    size: "0",
    description: "",
    amenities: "",
    images: [],
    contactName: contact.name,
    contactPhone: contact.phone,
    contactEmail: contact.email,
  };
}

function splitList(value: string): string[] {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

/** One "List a Room" workflow spanning property -> room -> listing -> photos ->
 *  review, so a host never has to separately visit "My Properties" and "My
 *  Listings" just to publish their first room. Existing standalone Property/Room
 *  management (and listing editing) are untouched -- this is an additional,
 *  friendlier entry point on top of the same backend endpoints, not a
 *  replacement. Review offers "Save as Draft" (create only, same as before) or
 *  "Submit for Review" (create, then immediately ask an admin to review it). */
export function ListARoomWizard({
  open,
  onClose,
  onCreated,
  contact,
}: {
  open: boolean;
  onClose: () => void;
  /** submitted is true when the host chose "Submit for Review" on the Review
   *  step, false when they chose "Save as Draft". */
  onCreated: (submitted: boolean) => void;
  contact: { name: string; phone: string; email: string };
}) {
  const [step, setStep] = useState(0);
  const [properties, setProperties] = useState<Property[]>([]);
  const [rooms, setRooms] = useState<Room[]>([]);
  const [loadingContext, setLoadingContext] = useState(true);

  const [propertyChoice, setPropertyChoice] = useState<PropertyChoice>({ mode: "new", address: "", city: "" });
  const [roomChoice, setRoomChoice] = useState<RoomChoice>({ mode: "new", size: "", hasEnsuite: false });
  const [details, setDetails] = useState<ListingDetailsForm>(emptyDetails(contact));

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    setStep(0);
    setError("");
    setDetails(emptyDetails(contact));
    setLoadingContext(true);
    listHostedProperties()
      .then((owned) => {
        setProperties(owned);
        setPropertyChoice(owned.length > 0 ? { mode: "existing", propertyId: owned[0].id } : { mode: "new", address: "", city: "" });
      })
      .catch(() => setProperties([]))
      .finally(() => setLoadingContext(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (propertyChoice.mode !== "existing") {
      setRooms([]);
      setRoomChoice({ mode: "new", size: "", hasEnsuite: false });
      return;
    }
    listHostedRooms(propertyChoice.propertyId)
      .then((found) => {
        setRooms(found);
        setRoomChoice(found.length > 0 ? { mode: "existing", roomId: found[0].id } : { mode: "new", size: "", hasEnsuite: false });
      })
      .catch(() => setRooms([]));
  }, [propertyChoice]);

  function goNext() {
    setError("");
    if (step === 0) {
      if (propertyChoice.mode === "new" && (!propertyChoice.address.trim() || !propertyChoice.city.trim())) {
        setError("Enter an address and city, or pick an existing property.");
        return;
      }
      // Reduce duplicate entry: default the listing's area/neighbourhood from the
      // property's own address instead of making the host retype it. Only fills
      // in an empty field -- never overwrites something they've already typed.
      if (!details.location.trim()) {
        const defaultLocation =
          propertyChoice.mode === "existing"
            ? properties.find((p) => p.id === propertyChoice.propertyId)?.address ?? ""
            : propertyChoice.address;
        if (defaultLocation) {
          setDetails((d) => ({ ...d, location: defaultLocation }));
        }
      }
    }
    if (step === 1) {
      if (roomChoice.mode === "new") {
        const size = Number(roomChoice.size || 0);
        if (!Number.isFinite(size) || size < 0) {
          setError("Enter a valid room size in square feet.");
          return;
        }
      }
    }
    if (step === 2) {
      if (!details.name.trim() || !details.location.trim()) {
        setError("Give the listing a name and an area/neighbourhood.");
        return;
      }
      const price = Number(details.pricePerNight);
      if (!Number.isFinite(price) || price <= 0) {
        setError("Enter a nightly price greater than zero.");
        return;
      }
      const minStay = Number(details.minStayNights);
      if (!Number.isFinite(minStay) || minStay < 30) {
        setError("Zoiko is a long-stay marketplace — the minimum stay must be at least 30 nights.");
        return;
      }
    }
    setStep((s) => Math.min(STEPS.length - 1, s + 1));
  }

  function goBack() {
    setError("");
    setStep((s) => Math.max(0, s - 1));
  }

  async function handleFinish(submitForReview: boolean) {
    const price = Number(details.pricePerNight);
    const minStay = Number(details.minStayNights);
    if (!details.name.trim() || !details.location.trim() || !Number.isFinite(price) || price <= 0 || !Number.isFinite(minStay) || minStay < 30) {
      // Should already be caught by goNext's per-step validation -- this only
      // guards against reaching Review some other way (e.g. browser back/forward).
      setError("Some listing details are missing or invalid. Go back to the Listing step to fix them.");
      return;
    }

    setError("");
    setSubmitting(true);
    try {
      let propertyId: number;
      let city: string;
      if (propertyChoice.mode === "existing") {
        propertyId = propertyChoice.propertyId;
        city = properties.find((p) => p.id === propertyId)?.city ?? "";
      } else {
        const created = await createHostedProperty({
          address: propertyChoice.address.trim(),
          city: propertyChoice.city.trim(),
        });
        propertyId = created.id;
        city = created.city;
      }

      let roomId: number;
      if (roomChoice.mode === "existing") {
        roomId = roomChoice.roomId;
      } else {
        const createdRoom = await createHostedRoom(propertyId, {
          size: Math.round(Number(roomChoice.size || 0)),
          hasEnsuite: roomChoice.hasEnsuite,
        });
        roomId = createdRoom.id;
      }

      const payload: HostedListingInput = {
        name: details.name.trim(),
        roomType: details.roomType.trim() || "Private room",
        city,
        location: details.location.trim(),
        pricePerNight: price,
        currency: details.currency,
        guests: Math.max(1, Number(details.guests) || 1),
        bedrooms: Number(details.bedrooms) || 0,
        bathrooms: Number(details.bathrooms) || 1,
        size: Number(details.size) || 0,
        description: details.description.trim(),
        amenities: splitList(details.amenities),
        images: details.images,
        minStayNights: Math.round(minStay),
        roomId,
        contactName: details.contactName.trim(),
        contactPhone: details.contactPhone.trim(),
        contactEmail: details.contactEmail.trim(),
      };
      const created = await createHostedListing(payload);
      if (submitForReview) {
        await submitHostedListingForReview(created.id);
      }
      onCreated(submitForReview);
    } catch (err) {
      setError(errorMessage(err, "Could not create the listing."));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="List a Room" size="xl">
      <div className="space-y-5">
        <div className="flex items-center gap-2">
          {STEPS.map((label, i) => (
            <div key={label} className="flex flex-1 items-center gap-2">
              <span
                className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-bold ${
                  i < step
                    ? "bg-primary-700 text-white"
                    : i === step
                      ? "bg-primary-100 text-primary-800 ring-2 ring-primary-600 dark:bg-primary-500/20 dark:text-primary-200"
                      : "bg-slate-100 text-slate-400 dark:bg-slate-800"
                }`}
              >
                {i < step ? <Check className="h-3.5 w-3.5" /> : i + 1}
              </span>
              <span className={`text-xs font-medium ${i === step ? "text-primary-900 dark:text-white" : "text-slate-400"}`}>
                {label}
              </span>
              {i < STEPS.length - 1 && <span className="h-px flex-1 bg-slate-100 dark:bg-slate-800" />}
            </div>
          ))}
        </div>

        {loadingContext ? (
          <p className="py-8 text-center text-sm text-slate-400">Loading your properties...</p>
        ) : (
          <div className="max-h-[55vh] space-y-4 overflow-y-auto pr-1">
            {step === 0 && (
              <div className="space-y-3">
                <p className="text-xs text-slate-500 dark:text-slate-400">Which property is this room in?</p>
                {properties.map((property) => (
                  <label
                    key={property.id}
                    className={`flex cursor-pointer items-center gap-3 rounded-xl px-4 py-3 ring-1 transition-colors ${
                      propertyChoice.mode === "existing" && propertyChoice.propertyId === property.id
                        ? "bg-primary-50 ring-primary-300 dark:bg-primary-500/10 dark:ring-primary-500/40"
                        : "bg-slate-50 ring-slate-100 dark:bg-slate-800/60 dark:ring-white/5"
                    }`}
                  >
                    <input
                      type="radio"
                      name="property-choice"
                      checked={propertyChoice.mode === "existing" && propertyChoice.propertyId === property.id}
                      onChange={() => setPropertyChoice({ mode: "existing", propertyId: property.id })}
                    />
                    <span>
                      <span className="block text-sm font-semibold text-slate-700 dark:text-slate-200">{property.address}</span>
                      <span className="block text-xs text-slate-400">{property.city}</span>
                    </span>
                  </label>
                ))}

                <label
                  className={`flex cursor-pointer items-center gap-3 rounded-xl px-4 py-3 ring-1 transition-colors ${
                    propertyChoice.mode === "new"
                      ? "bg-primary-50 ring-primary-300 dark:bg-primary-500/10 dark:ring-primary-500/40"
                      : "bg-slate-50 ring-slate-100 dark:bg-slate-800/60 dark:ring-white/5"
                  }`}
                >
                  <input
                    type="radio"
                    name="property-choice"
                    checked={propertyChoice.mode === "new"}
                    onChange={() => setPropertyChoice({ mode: "new", address: "", city: "" })}
                  />
                  <span className="flex items-center gap-1.5 text-sm font-semibold text-slate-700 dark:text-slate-200">
                    <Building2 className="h-4 w-4" /> Add a new property
                  </span>
                </label>

                {propertyChoice.mode === "new" && (
                  <div className="grid grid-cols-1 gap-3 pl-4 sm:grid-cols-2">
                    <Field label="Address">
                      <input
                        value={propertyChoice.address}
                        onChange={(e) => setPropertyChoice({ ...propertyChoice, address: e.target.value })}
                        placeholder="14 Linking Road, Bandra West"
                        className={inputClass}
                      />
                    </Field>
                    <Field label="City">
                      <input
                        value={propertyChoice.city}
                        onChange={(e) => setPropertyChoice({ ...propertyChoice, city: e.target.value })}
                        placeholder="Mumbai"
                        className={inputClass}
                      />
                    </Field>
                  </div>
                )}
              </div>
            )}

            {step === 1 && (
              <div className="space-y-3">
                <p className="text-xs text-slate-500 dark:text-slate-400">Which room are you listing?</p>
                {rooms.map((room) => (
                  <label
                    key={room.id}
                    className={`flex cursor-pointer items-center gap-3 rounded-xl px-4 py-3 ring-1 transition-colors ${
                      roomChoice.mode === "existing" && roomChoice.roomId === room.id
                        ? "bg-primary-50 ring-primary-300 dark:bg-primary-500/10 dark:ring-primary-500/40"
                        : "bg-slate-50 ring-slate-100 dark:bg-slate-800/60 dark:ring-white/5"
                    }`}
                  >
                    <input
                      type="radio"
                      name="room-choice"
                      checked={roomChoice.mode === "existing" && roomChoice.roomId === room.id}
                      onChange={() => setRoomChoice({ mode: "existing", roomId: room.id })}
                    />
                    <span className="block text-sm font-semibold text-slate-700 dark:text-slate-200">
                      Room #{room.id} — {room.size > 0 ? `${room.size} sq ft` : "size not set"} ·{" "}
                      {room.hasEnsuite ? "En-suite" : "Shared bathroom"}
                    </span>
                  </label>
                ))}

                <label
                  className={`flex cursor-pointer items-center gap-3 rounded-xl px-4 py-3 ring-1 transition-colors ${
                    roomChoice.mode === "new"
                      ? "bg-primary-50 ring-primary-300 dark:bg-primary-500/10 dark:ring-primary-500/40"
                      : "bg-slate-50 ring-slate-100 dark:bg-slate-800/60 dark:ring-white/5"
                  }`}
                >
                  <input
                    type="radio"
                    name="room-choice"
                    checked={roomChoice.mode === "new"}
                    onChange={() => setRoomChoice({ mode: "new", size: "", hasEnsuite: false })}
                  />
                  <span className="text-sm font-semibold text-slate-700 dark:text-slate-200">Add a new room</span>
                </label>

                {roomChoice.mode === "new" && (
                  <div className="space-y-3 pl-4">
                    <p className="rounded-xl bg-slate-50 px-4 py-3 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
                      Zoiko only hosts private rooms for 30+ night stays, so every room is created as a private room.
                    </p>
                    <Field label="Size (sq ft)">
                      <input
                        inputMode="numeric"
                        value={roomChoice.size}
                        onChange={(e) => setRoomChoice({ ...roomChoice, size: e.target.value })}
                        placeholder="180"
                        className={inputClass}
                      />
                    </Field>
                    <div className="flex items-center justify-between rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800/60">
                      <span className="text-sm font-medium text-slate-600 dark:text-slate-300">Has a private en-suite</span>
                      <Switch
                        checked={roomChoice.hasEnsuite}
                        onChange={(checked) => setRoomChoice({ ...roomChoice, hasEnsuite: checked })}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}

            {step === 2 && (
              <div className="space-y-4">
                <Field label="Listing name">
                  <input
                    value={details.name}
                    onChange={(e) => setDetails((d) => ({ ...d, name: e.target.value }))}
                    placeholder="Sunlit private room in Bandra West"
                    className={inputClass}
                  />
                </Field>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <Field label="Area / neighbourhood">
                    <input
                      value={details.location}
                      onChange={(e) => setDetails((d) => ({ ...d, location: e.target.value }))}
                      placeholder="Bandra West"
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Room type">
                    <input
                      value={details.roomType}
                      onChange={(e) => setDetails((d) => ({ ...d, roomType: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Price per night">
                    <input
                      inputMode="decimal"
                      value={details.pricePerNight}
                      onChange={(e) => setDetails((d) => ({ ...d, pricePerNight: e.target.value }))}
                      placeholder="1800"
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Currency">
                    <select
                      value={details.currency}
                      onChange={(e) => setDetails((d) => ({ ...d, currency: e.target.value }))}
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
                      value={details.minStayNights}
                      onChange={(e) => setDetails((d) => ({ ...d, minStayNights: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Guests">
                    <input
                      inputMode="numeric"
                      value={details.guests}
                      onChange={(e) => setDetails((d) => ({ ...d, guests: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Bedrooms">
                    <input
                      inputMode="numeric"
                      value={details.bedrooms}
                      onChange={(e) => setDetails((d) => ({ ...d, bedrooms: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Bathrooms">
                    <input
                      inputMode="numeric"
                      value={details.bathrooms}
                      onChange={(e) => setDetails((d) => ({ ...d, bathrooms: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Size (sq ft)">
                    <input
                      inputMode="numeric"
                      value={details.size}
                      onChange={(e) => setDetails((d) => ({ ...d, size: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                </div>

                <Field label="Description">
                  <textarea
                    value={details.description}
                    onChange={(e) => setDetails((d) => ({ ...d, description: e.target.value }))}
                    rows={3}
                    placeholder="Quiet furnished room with a study desk, 10 minutes from the station..."
                    className={inputClass}
                  />
                </Field>

                <Field label="Amenities" hint="Comma separated, e.g. Wi-Fi, Washing machine, Air conditioning">
                  <input
                    value={details.amenities}
                    onChange={(e) => setDetails((d) => ({ ...d, amenities: e.target.value }))}
                    className={inputClass}
                  />
                </Field>

                <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
                  <Field label="Contact name">
                    <input
                      value={details.contactName}
                      onChange={(e) => setDetails((d) => ({ ...d, contactName: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Contact phone">
                    <input
                      value={details.contactPhone}
                      onChange={(e) => setDetails((d) => ({ ...d, contactPhone: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                  <Field label="Contact email">
                    <input
                      value={details.contactEmail}
                      onChange={(e) => setDetails((d) => ({ ...d, contactEmail: e.target.value }))}
                      className={inputClass}
                    />
                  </Field>
                </div>
              </div>
            )}

            {step === 3 && (
              <div className="space-y-3">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Add photos of the room. The first photo is used as the cover image shown in search results.
                </p>
                <ImageGalleryUploader
                  images={details.images}
                  onChange={(images) => setDetails((d) => ({ ...d, images }))}
                  uploadUrl="/api/users/hosting/uploads/images"
                  maxImages={MAX_LISTING_IMAGES}
                />
              </div>
            )}

            {step === 4 && (
              <div className="space-y-4">
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  Review your listing before saving. You can save it as a draft and come back later, or submit it
                  for a Zoiko admin to review and publish.
                </p>

                <div className="overflow-hidden rounded-xl ring-1 ring-slate-100 dark:ring-white/10">
                  {details.images[0] ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img src={details.images[0]} alt={details.name} className="h-40 w-full object-cover" />
                  ) : (
                    <div className="flex h-24 w-full items-center justify-center bg-slate-50 text-xs text-slate-400 dark:bg-slate-800">
                      No photos added
                    </div>
                  )}
                  <div className="space-y-2 bg-slate-50 p-4 dark:bg-slate-800/60">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="font-heading text-sm font-bold text-primary-900 dark:text-white">
                        {details.name || "Untitled listing"}
                      </p>
                      <Badge tone="neutral">{details.roomType}</Badge>
                    </div>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {details.location || "No area set"}
                      {propertyChoice.mode === "existing"
                        ? ` — ${properties.find((p) => p.id === propertyChoice.propertyId)?.address ?? ""}`
                        : propertyChoice.address
                          ? ` — ${propertyChoice.address}, ${propertyChoice.city}`
                          : ""}
                    </p>
                    <p className="text-sm font-semibold text-primary-900 dark:text-white">
                      {details.pricePerNight ? formatCurrency(Number(details.pricePerNight) || 0, details.currency) : "—"}
                      <span className="text-xs font-normal text-slate-400"> / night · min. {details.minStayNights} nights</span>
                    </p>
                    <p className="text-xs text-slate-500 dark:text-slate-400">
                      {details.guests} guest{details.guests === "1" ? "" : "s"} · {details.bedrooms} bedroom
                      {details.bedrooms === "1" ? "" : "s"} · {details.bathrooms} bathroom{details.bathrooms === "1" ? "" : "s"}
                      {Number(details.size) > 0 ? ` · ${details.size} sq ft` : ""} · {details.images.length} photo
                      {details.images.length === 1 ? "" : "s"}
                    </p>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {error && (
          <p className="rounded-lg bg-accent-50 px-3 py-2 text-xs font-medium text-accent-700 ring-1 ring-accent-200">
            {error}
          </p>
        )}

        <div className="flex justify-between gap-2 pt-1">
          <Button type="button" variant="ghost" onClick={step === 0 ? onClose : goBack}>
            {step === 0 ? "Cancel" : (
              <>
                <ChevronLeft className="h-4 w-4" /> Back
              </>
            )}
          </Button>
          {step < STEPS.length - 1 ? (
            <Button type="button" onClick={goNext} disabled={loadingContext}>
              Next <ChevronRight className="h-4 w-4" />
            </Button>
          ) : (
            <div className="flex gap-2">
              <Button type="button" variant="outline" onClick={() => handleFinish(false)} loading={submitting}>
                <FileEdit className="h-4 w-4" /> Save as Draft
              </Button>
              <Button type="button" onClick={() => handleFinish(true)} loading={submitting}>
                <Send className="h-4 w-4" /> Submit for Review
              </Button>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
}
