"use client";

import { useEffect, useMemo, useState } from "react";
import dynamic from "next/dynamic";
import { useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  Ban,
  Building2,
  CheckCircle2,
  Contact,
  Copy,
  Eye,
  Globe,
  Mail,
  MapPin,
  Pencil,
  Phone,
  Plus,
  Rocket,
  Search,
  ShieldAlert,
  PauseCircle,
  SlidersHorizontal,
  Trash2,
  Users,
  XCircle,
} from "lucide-react";
import { AdminRole, Listing, ListingState, Property, PublishEligibility, Room } from "@/lib/types";
import { Badge } from "@/components/ui/Badge";
import { StarRating } from "@/components/ui/StarRating";
import { Button } from "@/components/ui/Button";
import { Modal } from "@/components/ui/Modal";
import { StatCard } from "@/components/admin/StatCard";
import { formatCurrency, resolveImageUrl } from "@/lib/utils";
import { unsplash } from "@/lib/images";
import { apiClientFetch } from "@/lib/api-client";
import { getCurrentAdmin } from "@/lib/auth";
import { listingStateLabel, listingStateTone } from "@/lib/status";
import { ImageGalleryUploader } from "@/components/admin/ImageGalleryUploader";

const LocationPicker = dynamic(() => import("@/components/admin/LocationPicker").then((m) => m.LocationPicker), {
  ssr: false,
  loading: () => <div className="h-[220px] w-full animate-pulse rounded-xl bg-slate-100 dark:bg-slate-800" />,
});

type RoomOption = Room & { property: Property };

const emptyForm = {
  name: "",
  roomType: "Private Room",
  city: "",
  location: "",
  price: "",
  guests: "1",
  bedrooms: "1",
  bathrooms: "1",
  size: "120",
  minStayNights: "30",
  description: "",
  amenities: "",
  tags: "",
  latitude: null as number | null,
  longitude: null as number | null,
  images: [] as string[],
  roomId: "",
  contactName: "",
  contactPhone: "",
  contactEmail: "",
};

const emptyNewRoom = { address: "", city: "", size: "120", hasEnsuite: false };

export function PropertiesManager({ initialListings }: { initialListings: Listing[] }) {
  const [items, setItems] = useState(initialListings);
  const searchParams = useSearchParams();
  const [query, setQuery] = useState(() => searchParams.get("q") ?? "");
  const [stateFilter, setStateFilter] = useState<ListingState | "all">("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [toast, setToast] = useState("");
  const [form, setForm] = useState(emptyForm);
  const [role, setRole] = useState<AdminRole | null>(null);
  const [roomOptions, setRoomOptions] = useState<RoomOption[]>([]);
  const [addingRoom, setAddingRoom] = useState(false);
  const [newRoom, setNewRoom] = useState(emptyNewRoom);
  const [reviewListing, setReviewListing] = useState<Listing | null>(null);
  const [reviewSignals, setReviewSignals] = useState<PublishEligibility | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [reviewBusy, setReviewBusy] = useState(false);

  useEffect(() => {
    getCurrentAdmin().then((admin) => setRole(admin?.role ?? null));
  }, []);

  // Deep-link from a "listing pending review" notification (see
  // resolveNotificationHref in lib/notifications.ts) -- open straight to the
  // review flow for that listing rather than making the admin find it manually.
  useEffect(() => {
    const targetId = searchParams.get("listingId");
    if (!targetId) return;
    const target = items.find((l) => l.id === targetId);
    if (target) {
      if (target.state === "REVIEW") {
        openReview(target);
      } else {
        openEditModal(target);
      }
    } else {
      setToast("That listing could not be found — it may have been removed.");
    }
    // Only ever react to the query param changing, not to every items/toast update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  async function loadRoomOptions() {
    try {
      // Two requests total regardless of property count -- was previously one
      // request per property (N+1), which visibly slows down once an admin
      // has more than a handful of properties.
      const [properties, rooms] = await Promise.all([
        apiClientFetch<Property[]>("/api/properties"),
        apiClientFetch<Room[]>("/api/properties/rooms"),
      ]);
      const propertyById = new Map(properties.map((p) => [p.id, p]));
      setRoomOptions(
        rooms.flatMap((r) => {
          const property = propertyById.get(r.propertyId);
          return property ? [{ ...r, property }] : [];
        })
      );
    } catch {
      // Room picker just stays empty; the modal still surfaces a message.
    }
  }

  useEffect(() => {
    loadRoomOptions();
  }, []);

  const presentStates = useMemo(() => {
    const seen = new Set<ListingState>();
    items.forEach((l) => seen.add(l.state));
    return Array.from(seen);
  }, [items]);

  const filtered = useMemo(() => {
    return items.filter((l) => {
      const matchesState = stateFilter === "all" || l.state === stateFilter;
      const q = query.trim().toLowerCase();
      const matchesQuery = !q || l.name.toLowerCase().includes(q) || l.city.toLowerCase().includes(q);
      return matchesState && matchesQuery;
    });
  }, [items, query, stateFilter]);

  const stats = useMemo(() => {
    const published = items.filter((l) => l.state === "PUBLISHED").length;
    const draft = items.filter((l) => l.state === "DRAFT" || l.state === "EVIDENCE_PENDING" || l.state === "REVIEW").length;
    const avgPrice = items.length ? Math.round(items.reduce((s, l) => s + l.pricePerNight, 0) / items.length) : 0;
    return { total: items.length, published, draft, avgPrice };
  }, [items]);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 3200);
  }

  async function removeListing(id: string) {
    try {
      await apiClientFetch(`/api/listings/${id}`, { method: "DELETE" });
      setItems((prev) => prev.filter((l) => l.id !== id));
      showToast("Listing removed");
    } catch {
      showToast("Failed to remove listing");
    }
  }

  async function duplicateListing(listing: Listing) {
    try {
      const copy = await apiClientFetch<Listing>(`/api/listings/${listing.id}/duplicate`, { method: "POST" });
      setItems((prev) => [copy, ...prev]);
      showToast("Listing duplicated as a new draft");
    } catch {
      showToast("Failed to duplicate listing");
    }
  }

  async function publishListing(id: string) {
    // Publishing is the admin's own decision -- it does not re-check authority /
    // occupancy / identity signals (those are informational, surfaced in the
    // review modal, never a hard gate here).
    try {
      const updated = await apiClientFetch<Listing>(`/api/listings/${id}/publish`, { method: "POST" });
      setItems((prev) => prev.map((l) => (l.id === id ? updated : l)));
      showToast("Listing published");
    } catch {
      showToast("Failed to publish listing");
    }
  }

  async function transitionListing(id: string, action: "pause" | "withdraw" | "suspend") {
    try {
      const updated = await apiClientFetch<Listing>(`/api/listings/${id}/${action}`, { method: "POST" });
      setItems((prev) => prev.map((l) => (l.id === id ? updated : l)));
      showToast(
        action === "pause" ? "Listing paused" : action === "withdraw" ? "Listing withdrawn" : "Listing suspended"
      );
    } catch {
      showToast(`Failed to ${action} listing`);
    }
  }

  async function openReview(listing: Listing) {
    setReviewListing(listing);
    setRejectReason("");
    setReviewSignals(null);
    try {
      const signals = await apiClientFetch<PublishEligibility>(`/api/listings/${listing.id}/publish-eligibility`);
      setReviewSignals(signals);
    } catch {
      // Informational only -- the review modal still works without these signals.
    }
  }

  function closeReview() {
    setReviewListing(null);
    setReviewSignals(null);
    setRejectReason("");
  }

  async function approveAndPublish() {
    if (!reviewListing) return;
    setReviewBusy(true);
    try {
      // Two explicit steps -- REVIEW -> APPROVED -> PUBLISHED -- so the approval
      // decision is recorded independently of publication (a later pause/republish
      // never has to re-approve). One click for the reviewer, two real transitions.
      await apiClientFetch<Listing>(`/api/listings/${reviewListing.id}/approve`, { method: "POST" });
      const updated = await apiClientFetch<Listing>(`/api/listings/${reviewListing.id}/publish`, { method: "POST" });
      setItems((prev) => prev.map((l) => (l.id === updated.id ? updated : l)));
      showToast(`"${updated.name}" approved and published`);
      closeReview();
    } catch {
      showToast("Failed to approve and publish this listing");
    } finally {
      setReviewBusy(false);
    }
  }

  async function rejectReviewListing() {
    if (!reviewListing || !rejectReason.trim()) return;
    setReviewBusy(true);
    try {
      const updated = await apiClientFetch<Listing>(`/api/listings/${reviewListing.id}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason: rejectReason.trim() }),
      });
      setItems((prev) => prev.map((l) => (l.id === updated.id ? updated : l)));
      showToast(`"${updated.name}" rejected`);
      closeReview();
    } catch {
      showToast("Failed to reject this listing");
    } finally {
      setReviewBusy(false);
    }
  }

  function openAddModal() {
    setEditingId(null);
    setForm(emptyForm);
    setAddingRoom(false);
    setNewRoom(emptyNewRoom);
    setModalOpen(true);
  }

  function openEditModal(listing: Listing) {
    setEditingId(listing.id);
    setForm({
      name: listing.name,
      roomType: listing.roomType,
      city: listing.city,
      location: listing.location,
      price: String(listing.pricePerNight),
      guests: String(listing.guests),
      bedrooms: String(listing.bedrooms),
      bathrooms: String(listing.bathrooms),
      size: String(listing.size),
      minStayNights: String(listing.minStayNights),
      description: listing.description,
      amenities: listing.amenities.join(", "),
      tags: listing.tags.join(", "),
      latitude: listing.latitude ?? null,
      longitude: listing.longitude ?? null,
      images: [...listing.images],
      roomId: listing.roomId ? String(listing.roomId) : "",
      contactName: listing.contactName,
      contactPhone: listing.contactPhone,
      contactEmail: listing.contactEmail,
    });
    setAddingRoom(false);
    setNewRoom(emptyNewRoom);
    setModalOpen(true);
  }

  async function createPropertyAndRoom() {
    if (!newRoom.address.trim() || !newRoom.city.trim()) {
      showToast("Enter an address and city for the new property");
      return;
    }
    try {
      const property = await apiClientFetch<Property>("/api/properties", {
        method: "POST",
        body: JSON.stringify({ address: newRoom.address.trim(), city: newRoom.city.trim() }),
      });
      const room = await apiClientFetch<Room>(`/api/properties/${property.id}/rooms`, {
        method: "POST",
        body: JSON.stringify({ size: Number(newRoom.size) || 0, hasEnsuite: newRoom.hasEnsuite }),
      });
      const option: RoomOption = { ...room, property };
      setRoomOptions((prev) => [...prev, option]);
      setForm((f) => ({ ...f, roomId: String(room.id), city: f.city.trim() || property.city }));
      setAddingRoom(false);
      setNewRoom(emptyNewRoom);
      showToast("Property and room created");
    } catch {
      showToast("Failed to create property and room");
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim() || !form.city.trim() || !form.price) return;
    if (!form.roomId) {
      showToast("Select or create a room for this listing");
      return;
    }
    if (Number(form.minStayNights) < 30) {
      showToast("Minimum stay must be at least 30 nights");
      return;
    }

    const amenities = form.amenities
      .split(",")
      .map((a) => a.trim())
      .filter(Boolean);
    const tags = form.tags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    try {
      if (editingId) {
        const updated = await apiClientFetch<Listing>(`/api/listings/${editingId}`, {
          method: "PUT",
          body: JSON.stringify({
            name: form.name.trim(),
            roomType: form.roomType.trim() || "Private Room",
            city: form.city.trim(),
            location: form.location.trim() || form.city.trim(),
            latitude: form.latitude,
            longitude: form.longitude,
            pricePerNight: Number(form.price),
            guests: Number(form.guests),
            bedrooms: Number(form.bedrooms),
            bathrooms: Number(form.bathrooms),
            size: Number(form.size),
            minStayNights: Number(form.minStayNights),
            roomId: Number(form.roomId),
            images: form.images,
            description: form.description.trim() || undefined,
            amenities: amenities.length ? amenities : undefined,
            tags: tags.length ? tags : undefined,
            contactName: form.contactName.trim(),
            contactPhone: form.contactPhone.trim(),
            contactEmail: form.contactEmail.trim(),
          }),
        });
        setItems((prev) => prev.map((l) => (l.id === editingId ? updated : l)));
        showToast("Listing updated successfully");
      } else {
        const created = await apiClientFetch<Listing>("/api/listings", {
          method: "POST",
          body: JSON.stringify({
            name: form.name.trim(),
            roomType: form.roomType.trim() || "Private Room",
            city: form.city.trim(),
            location: form.location.trim() || form.city.trim(),
            latitude: form.latitude,
            longitude: form.longitude,
            pricePerNight: Number(form.price),
            guests: Number(form.guests),
            bedrooms: Number(form.bedrooms),
            bathrooms: Number(form.bathrooms),
            size: Number(form.size),
            minStayNights: Number(form.minStayNights),
            roomId: Number(form.roomId),
            images: form.images.length ? form.images : [unsplash("hotelBedroom")],
            amenities: amenities.length ? amenities : ["Free WiFi"],
            description: form.description.trim() || "Newly added listing — details coming soon.",
            tags: tags.length ? tags : ["New"],
            contactName: form.contactName.trim(),
            contactPhone: form.contactPhone.trim(),
            contactEmail: form.contactEmail.trim(),
          }),
        });
        setItems((prev) => [created, ...prev]);
        showToast("Listing created as a draft — publish once evidence is verified");
      }

      setModalOpen(false);
      setEditingId(null);
      setForm(emptyForm);
    } catch {
      showToast(editingId ? "Failed to update listing" : "Failed to create listing");
    }
  }

  return (
    <div>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Total Listings" value={String(stats.total)} change="Live" icon={SlidersHorizontal} index={0} />
        <StatCard label="Live on Website" value={String(stats.published)} change="Via API" icon={Globe} index={1} />
        <StatCard label="Draft / In Review" value={String(stats.draft)} change="Awaiting evidence" icon={Building2} index={2} />
        <StatCard label="Avg. Price" value={formatCurrency(stats.avgPrice)} change="Blended" icon={Building2} index={3} />
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3 rounded-2xl bg-white p-5 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400 dark:text-slate-400" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search listings by name or city"
            className="w-full rounded-full bg-slate-50 py-2.5 pl-10 pr-4 text-sm outline-none ring-1 ring-slate-100 transition-all focus:ring-primary-300 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
          />
        </div>
        <div className="flex flex-wrap gap-2">
          {(["all", ...presentStates] as Array<ListingState | "all">).map((s) => (
            <button
              key={s}
              onClick={() => setStateFilter(s)}
              className={`rounded-full px-3.5 py-1.5 text-xs font-semibold transition-all duration-200 ${
                stateFilter === s
                  ? "bg-primary-700 text-white shadow-md shadow-primary-900/25"
                  : "bg-slate-50 text-slate-500 hover:bg-primary-50 hover:text-primary-700 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
              }`}
            >
              {s === "all" ? "All States" : listingStateLabel[s]}
            </button>
          ))}
        </div>
        <Button variant="accent" size="sm" onClick={openAddModal}>
          <Plus className="h-4 w-4" /> Add Listing
        </Button>
      </div>

      <div className="mt-6 grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((listing, i) => (
          <div
            key={listing.id}
            className="animate-fade-up overflow-hidden rounded-2xl bg-white shadow-sm ring-1 ring-slate-100 transition-shadow duration-300 hover:shadow-lg dark:bg-slate-900 dark:ring-white/10"
            style={{ animationDelay: `${Math.min(i, 8) * 0.05}s` }}
          >
            <div className="relative h-40 w-full">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={resolveImageUrl(listing.images[0])} alt={listing.name} className="h-full w-full object-cover" />
              <Badge tone="primary" className="absolute left-3 top-3">
                {listing.minStayNights}+ nights
              </Badge>
              {listing.state === "PUBLISHED" && !listing.available ? (
                <Badge tone="warning" className="absolute right-3 top-3">
                  Occupied
                </Badge>
              ) : (
                <Badge tone={listingStateTone[listing.state]} className="absolute right-3 top-3">
                  {listingStateLabel[listing.state]}
                </Badge>
              )}
            </div>
            <div className="p-4">
              <h3 className="truncate font-heading text-sm font-bold text-primary-900 dark:text-white">{listing.name}</h3>
              <p className="truncate text-xs text-slate-400 dark:text-slate-400">{listing.roomType}</p>
              {listing.location && listing.location !== listing.city && (
                <p className="mt-0.5 flex items-center gap-1 truncate text-xs text-slate-500 dark:text-slate-400">
                  <MapPin className="h-3 w-3 shrink-0" /> {listing.location}
                </p>
              )}

              <div className="mt-2 flex flex-wrap gap-2">
                {listing.state === "REVIEW" && (
                  <Button size="sm" variant="primary" className="flex-1" onClick={() => openReview(listing)}>
                    <Eye className="h-3.5 w-3.5" /> Review
                  </Button>
                )}
                {listing.state !== "PUBLISHED" && listing.state !== "REVIEW" && (
                  <Button size="sm" variant="primary" className="flex-1" onClick={() => publishListing(listing.id)}>
                    <Rocket className="h-3.5 w-3.5" /> Publish
                  </Button>
                )}
                {listing.state === "PUBLISHED" && (
                  <Button size="sm" variant="outline" className="flex-1" onClick={() => transitionListing(listing.id, "pause")}>
                    <PauseCircle className="h-3.5 w-3.5" /> Pause
                  </Button>
                )}
                {listing.state !== "WITHDRAWN" && listing.state !== "ARCHIVED" && (
                  <Button size="sm" variant="outline" className="flex-1" onClick={() => transitionListing(listing.id, "withdraw")}>
                    <Ban className="h-3.5 w-3.5" /> Withdraw
                  </Button>
                )}
                {role === "super_admin" && listing.state !== "SUSPENDED" && (
                  <Button size="sm" variant="outline" className="flex-1" onClick={() => transitionListing(listing.id, "suspend")}>
                    <ShieldAlert className="h-3.5 w-3.5" /> Suspend
                  </Button>
                )}
              </div>

              <div className="mt-2.5 flex items-center justify-between text-xs text-slate-500 dark:text-slate-400">
                <span className="flex items-center gap-1">
                  <MapPin className="h-3 w-3" /> {listing.city}
                </span>
                <span className="flex items-center gap-1">
                  <Users className="h-3 w-3" /> {listing.guests}
                </span>
              </div>
              {(listing.contactName || listing.contactPhone || listing.contactEmail) && (
                <div className="mt-2 space-y-1 rounded-lg bg-slate-50 p-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                  {listing.contactName && <p className="flex items-center gap-1.5 font-semibold"><Contact className="h-3 w-3 shrink-0" /> {listing.contactName}</p>}
                  {listing.contactPhone && <p className="flex items-center gap-1.5"><Phone className="h-3 w-3 shrink-0" /> {listing.contactPhone}</p>}
                  {listing.contactEmail && <p className="flex items-center gap-1.5 truncate"><Mail className="h-3 w-3 shrink-0" /> {listing.contactEmail}</p>}
                </div>
              )}
              <div className="mt-2 flex items-center justify-between">
                <StarRating rating={listing.rating} size={12} reviewCount={listing.reviewCount} />
                <span className="text-sm font-bold text-primary-800 dark:text-primary-200">
                  {formatCurrency(listing.pricePerNight, listing.currency)}
                  <span className="text-xs font-medium text-slate-400 dark:text-slate-400">/night</span>
                </span>
              </div>

              <div className="mt-3 flex items-center justify-end gap-1 border-t border-slate-100 pt-3 dark:border-slate-800">
                <button
                  onClick={() => duplicateListing(listing)}
                  title="Duplicate"
                  className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
                >
                  <Copy className="h-4 w-4" />
                </button>
                <button
                  onClick={() => openEditModal(listing)}
                  title="Edit"
                  className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-primary-50 hover:text-primary-700 dark:text-slate-400 dark:hover:bg-primary-500/10 dark:hover:text-primary-300"
                >
                  <Pencil className="h-4 w-4" />
                </button>
                <button
                  onClick={() => removeListing(listing.id)}
                  title="Delete"
                  className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-accent-50 hover:text-accent-600 dark:text-slate-400 dark:hover:bg-accent-500/10"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {filtered.length === 0 && (
        <p className="mt-10 text-center text-sm text-slate-400 dark:text-slate-400">No listings match your search.</p>
      )}

      <Modal open={modalOpen} onClose={() => setModalOpen(false)} title={editingId ? "Edit Listing" : "Add New Listing"}>
        <form onSubmit={handleSubmit} autoComplete="off" className="max-h-[70vh] space-y-3.5 overflow-y-auto pr-1">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Listing Name
            </label>
            <input
              value={form.name}
              onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="e.g. Sunny Private Room in Koramangala Apartment"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Photos
            </label>
            <ImageGalleryUploader images={form.images} onChange={(images) => setForm((f) => ({ ...f, images }))} />
          </div>

          <div>
            <div className="mb-1 flex items-center justify-between">
              <label className="block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Room
              </label>
              <button
                type="button"
                onClick={() => setAddingRoom((v) => !v)}
                className="text-xs font-semibold text-primary-700 hover:underline dark:text-primary-300"
              >
                {addingRoom ? "Cancel" : "+ New property & room"}
              </button>
            </div>
            {!addingRoom ? (
              <select
                value={form.roomId}
                onChange={(e) => setForm((f) => ({ ...f, roomId: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              >
                <option value="">Select a room…</option>
                {roomOptions.map((room) => (
                  <option key={room.id} value={room.id}>
                    {room.property.address} — Room #{room.id}
                    {room.hasEnsuite ? " (ensuite)" : ""}
                  </option>
                ))}
              </select>
            ) : (
              <div className="space-y-2 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-200 dark:bg-slate-800 dark:ring-slate-700">
                <input
                  value={newRoom.address}
                  onChange={(e) => setNewRoom((r) => ({ ...r, address: e.target.value }))}
                  placeholder="Property address"
                  className="w-full rounded-lg bg-white px-3 py-2 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
                />
                <div className="grid grid-cols-2 gap-2">
                  <input
                    value={newRoom.city}
                    onChange={(e) => setNewRoom((r) => ({ ...r, city: e.target.value }))}
                    placeholder="City"
                    className="w-full rounded-lg bg-white px-3 py-2 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
                  />
                  <input
                    type="number"
                    min={0}
                    value={newRoom.size}
                    onChange={(e) => setNewRoom((r) => ({ ...r, size: e.target.value }))}
                    placeholder="Room size (sqft)"
                    className="w-full rounded-lg bg-white px-3 py-2 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
                  />
                </div>
                <label className="flex items-center gap-2 text-xs text-slate-500 dark:text-slate-400">
                  <input
                    type="checkbox"
                    checked={newRoom.hasEnsuite}
                    onChange={(e) => setNewRoom((r) => ({ ...r, hasEnsuite: e.target.checked }))}
                  />
                  Has ensuite bathroom
                </label>
                <Button type="button" size="sm" variant="primary" fullWidth onClick={createPropertyAndRoom}>
                  Create property & room
                </Button>
              </div>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                City
              </label>
              <input
                value={form.city}
                onChange={(e) => setForm((f) => ({ ...f, city: e.target.value }))}
                placeholder="e.g. Bengaluru"
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                required
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Price /night (₹)
              </label>
              <input
                type="number"
                min={0}
                value={form.price}
                onChange={(e) => setForm((f) => ({ ...f, price: e.target.value }))}
                placeholder="650"
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                required
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Minimum Stay (nights)
            </label>
            <input
              type="number"
              min={30}
              value={form.minStayNights}
              onChange={(e) => setForm((f) => ({ ...f, minStayNights: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              required
            />
            <p className="mt-1 text-xs text-slate-400 dark:text-slate-400">
              Every listing on this marketplace requires a 30+ night minimum stay.
            </p>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Contact Info <span className="font-normal normal-case text-slate-400">(shown to renters — leave blank to use your account details)</span>
            </label>
            <div className="space-y-2">
              <input
                value={form.contactName}
                onChange={(e) => setForm((f) => ({ ...f, contactName: e.target.value }))}
                placeholder="Contact name"
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
              <div className="grid grid-cols-2 gap-2">
                <input
                  type="tel"
                  value={form.contactPhone}
                  onChange={(e) => setForm((f) => ({ ...f, contactPhone: e.target.value }))}
                  placeholder="Phone number"
                  className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                />
                <input
                  type="email"
                  value={form.contactEmail}
                  onChange={(e) => setForm((f) => ({ ...f, contactEmail: e.target.value }))}
                  placeholder="Contact email"
                  className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
                />
              </div>
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Map Location {form.latitude != null && <span className="font-normal normal-case text-slate-400">({form.latitude.toFixed(4)}, {form.longitude?.toFixed(4)})</span>}
            </label>
            <LocationPicker
              latitude={form.latitude}
              longitude={form.longitude}
              onChange={(lat, lng) => setForm((f) => ({ ...f, latitude: lat, longitude: lng }))}
              onAddressResolved={(address) => setForm((f) => ({ ...f, location: address }))}
            />
            {form.location && (
              <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                Address: <span className="font-medium text-slate-700 dark:text-slate-200">{form.location}</span>
              </p>
            )}
          </div>

          <div className="grid grid-cols-4 gap-3">
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Guests
              </label>
              <input
                type="number"
                min={1}
                value={form.guests}
                onChange={(e) => setForm((f) => ({ ...f, guests: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Beds
              </label>
              <input
                type="number"
                min={0}
                value={form.bedrooms}
                onChange={(e) => setForm((f) => ({ ...f, bedrooms: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Baths
              </label>
              <input
                type="number"
                min={0}
                value={form.bathrooms}
                onChange={(e) => setForm((f) => ({ ...f, bathrooms: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                Size (sqft)
              </label>
              <input
                type="number"
                min={0}
                value={form.size}
                onChange={(e) => setForm((f) => ({ ...f, size: e.target.value }))}
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Description
            </label>
            <textarea
              value={form.description}
              onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
              rows={3}
              placeholder="Short description guests will see..."
              className="w-full resize-none rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Amenities (comma separated)
            </label>
            <input
              value={form.amenities}
              onChange={(e) => setForm((f) => ({ ...f, amenities: e.target.value }))}
              placeholder="Free WiFi, Shared Kitchen, Housekeeping"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>

          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Tags (comma separated)
            </label>
            <input
              value={form.tags}
              onChange={(e) => setForm((f) => ({ ...f, tags: e.target.value }))}
              placeholder="Long Stay, Furnished"
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>

          <Button type="submit" variant="primary" fullWidth className="mt-2">
            {editingId ? (
              <>
                <Pencil className="h-4 w-4" /> Save Changes
              </>
            ) : (
              <>
                <Plus className="h-4 w-4" /> Add Listing
              </>
            )}
          </Button>
        </form>
      </Modal>

      <Modal open={Boolean(reviewListing)} onClose={closeReview} title="Review listing">
        {reviewListing && (
          <div className="max-h-[70vh] space-y-4 overflow-y-auto pr-1">
            {reviewListing.images.length > 0 && (
              <div className="grid grid-cols-3 gap-2">
                {reviewListing.images.slice(0, 6).map((src, i) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    key={src + i}
                    src={resolveImageUrl(src)}
                    alt={`${reviewListing.name} photo ${i + 1}`}
                    className="aspect-square w-full rounded-lg object-cover"
                  />
                ))}
              </div>
            )}

            <div>
              <h3 className="font-heading text-base font-bold text-primary-900 dark:text-white">{reviewListing.name}</h3>
              <p className="mt-0.5 flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400">
                <MapPin className="h-3 w-3 shrink-0" /> {reviewListing.location}, {reviewListing.city}
              </p>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">
              <div className="rounded-lg bg-slate-50 p-2.5 dark:bg-slate-800">
                <p className="text-slate-400">Price</p>
                <p className="mt-0.5 font-semibold text-primary-900 dark:text-white">
                  {formatCurrency(reviewListing.pricePerNight, reviewListing.currency)} / night
                </p>
              </div>
              <div className="rounded-lg bg-slate-50 p-2.5 dark:bg-slate-800">
                <p className="text-slate-400">Minimum stay</p>
                <p className="mt-0.5 font-semibold text-primary-900 dark:text-white">{reviewListing.minStayNights} nights</p>
              </div>
              <div className="rounded-lg bg-slate-50 p-2.5 dark:bg-slate-800">
                <p className="text-slate-400">Room</p>
                <p className="mt-0.5 font-semibold text-primary-900 dark:text-white">
                  {reviewListing.roomId !== null ? `#${reviewListing.roomId}` : "—"}
                </p>
              </div>
              <div className="rounded-lg bg-slate-50 p-2.5 dark:bg-slate-800">
                <p className="text-slate-400">Guests / Beds / Baths</p>
                <p className="mt-0.5 font-semibold text-primary-900 dark:text-white">
                  {reviewListing.guests} / {reviewListing.bedrooms} / {reviewListing.bathrooms}
                </p>
              </div>
              <div className="rounded-lg bg-slate-50 p-2.5 dark:bg-slate-800">
                <p className="text-slate-400">Submitted by</p>
                <p className="mt-0.5 font-semibold text-primary-900 dark:text-white">
                  {reviewListing.partyId !== null ? "Host (USER account)" : "Admin-created"}
                </p>
              </div>
            </div>

            {(reviewListing.contactName || reviewListing.contactPhone || reviewListing.contactEmail) && (
              <div className="space-y-1 rounded-lg bg-slate-50 p-3 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Host contact</p>
                {reviewListing.contactName && (
                  <p className="flex items-center gap-1.5 font-semibold"><Contact className="h-3 w-3 shrink-0" /> {reviewListing.contactName}</p>
                )}
                {reviewListing.contactPhone && (
                  <p className="flex items-center gap-1.5"><Phone className="h-3 w-3 shrink-0" /> {reviewListing.contactPhone}</p>
                )}
                {reviewListing.contactEmail && (
                  <p className="flex items-center gap-1.5 truncate"><Mail className="h-3 w-3 shrink-0" /> {reviewListing.contactEmail}</p>
                )}
              </div>
            )}

            {reviewListing.amenities.length > 0 && (
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Amenities</p>
                <p className="text-xs text-slate-600 dark:text-slate-300">{reviewListing.amenities.join(", ")}</p>
              </div>
            )}

            {reviewListing.description && (
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Description</p>
                <p className="text-xs text-slate-600 dark:text-slate-300">{reviewListing.description}</p>
              </div>
            )}

            {reviewSignals && (
              <div
                className={`rounded-xl px-3 py-2.5 text-xs ring-1 ${
                  reviewSignals.eligible
                    ? "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-500/20"
                    : "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/20"
                }`}
              >
                {reviewSignals.eligible ? (
                  <p className="flex items-center gap-1.5 font-semibold">
                    <CheckCircle2 className="h-3.5 w-3.5" /> No compliance warnings.
                  </p>
                ) : (
                  <>
                    <p className="flex items-center gap-1.5 font-semibold">
                      <AlertTriangle className="h-3.5 w-3.5" /> For your attention (does not block approval):
                    </p>
                    <ul className="mt-1 list-disc space-y-0.5 pl-5">
                      {reviewSignals.reasons.map((reason) => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  </>
                )}
              </div>
            )}

            <div>
              <label className="mb-1 block text-[11px] font-semibold uppercase tracking-wide text-slate-400">
                Rejection reason (required to reject)
              </label>
              <textarea
                value={rejectReason}
                onChange={(e) => setRejectReason(e.target.value)}
                rows={2}
                placeholder="Explain what needs to change before this can be approved..."
                className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
              />
            </div>

            <div className="flex justify-end gap-2 pt-1">
              <Button
                type="button"
                variant="outline"
                loading={reviewBusy}
                disabled={!rejectReason.trim()}
                onClick={rejectReviewListing}
              >
                <XCircle className="h-4 w-4" /> Reject
              </Button>
              <Button type="button" variant="primary" loading={reviewBusy} onClick={approveAndPublish}>
                <CheckCircle2 className="h-4 w-4" /> Approve &amp; Publish
              </Button>
            </div>
          </div>
        )}
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex max-w-sm items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
