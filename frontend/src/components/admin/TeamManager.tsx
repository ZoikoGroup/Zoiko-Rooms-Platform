"use client";

import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Building2, CheckCircle2, ChevronRight, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Switch } from "@/components/ui/Switch";
import { Badge } from "@/components/ui/Badge";
import { Modal } from "@/components/ui/Modal";
import { StarRating } from "@/components/ui/StarRating";
import { getCurrentAdmin } from "@/lib/auth";
import { apiClientFetch, ApiError } from "@/lib/api-client";
import { AdminRole, AdminUserSummary, Listing } from "@/lib/types";
import { listingStateLabel, listingStateTone } from "@/lib/status";
import { cn, formatCurrency, formatDate, resolveImageUrl } from "@/lib/utils";

const emptyAdminForm = { email: "", password: "", fullName: "", phone: "", role: "admin" as AdminRole };

export function TeamManager() {
  const [adminUsers, setAdminUsers] = useState<AdminUserSummary[]>([]);
  const [listings, setListings] = useState<Listing[]>([]);
  const [adminForm, setAdminForm] = useState(emptyAdminForm);
  const [currentAdminId, setCurrentAdminId] = useState<number | null>(null);
  const [toast, setToast] = useState("");
  const [viewingAdmin, setViewingAdmin] = useState<AdminUserSummary | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<AdminUserSummary | null>(null);
  const [reassignToId, setReassignToId] = useState("");
  const [confirmForceDelete, setConfirmForceDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    getCurrentAdmin().then((admin) => {
      if (admin) setCurrentAdminId(admin.id);
    });
    apiClientFetch<AdminUserSummary[]>("/api/admin-users").then(setAdminUsers).catch(() => {});
    apiClientFetch<Listing[]>("/api/listings").then(setListings).catch(() => {});
  }, []);

  const listingsByOwner = useMemo(() => {
    const map = new Map<number, Listing[]>();
    for (const listing of listings) {
      const list = map.get(listing.ownerId) ?? [];
      list.push(listing);
      map.set(listing.ownerId, list);
    }
    return map;
  }, [listings]);

  const viewingAdminListings = viewingAdmin ? listingsByOwner.get(viewingAdmin.id) ?? [] : [];
  const deleteTargetListings = deleteTarget ? listingsByOwner.get(deleteTarget.id) ?? [] : [];
  const reassignOptions = adminUsers.filter((u) => u.id !== deleteTarget?.id);

  function showToast(message: string) {
    setToast(message);
    setTimeout(() => setToast(""), 2200);
  }

  async function handleCreateAdmin(e: React.FormEvent) {
    e.preventDefault();
    if (!adminForm.email.trim() || !adminForm.password.trim() || !adminForm.fullName.trim()) {
      showToast("Please fill in name, email and password");
      return;
    }
    try {
      const created = await apiClientFetch<AdminUserSummary>("/api/admin-users", {
        method: "POST",
        body: JSON.stringify({
          email: adminForm.email.trim(),
          password: adminForm.password,
          fullName: adminForm.fullName.trim(),
          phone: adminForm.phone.trim(),
          role: adminForm.role,
        }),
      });
      setAdminUsers((prev) => [...prev, created]);
      setAdminForm(emptyAdminForm);
      showToast(`${created.role === "super_admin" ? "Super admin" : "Admin"} account created`);
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to create admin account");
    }
  }

  async function handleChangeAdminRole(id: number, role: AdminRole) {
    try {
      const updated = await apiClientFetch<AdminUserSummary>(`/api/admin-users/${id}`, {
        method: "PUT",
        body: JSON.stringify({ role }),
      });
      setAdminUsers((prev) => prev.map((u) => (u.id === id ? updated : u)));
      showToast("Role updated");
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to update role");
    }
  }

  async function handleToggleAdminActive(id: number, isActive: boolean) {
    try {
      const updated = await apiClientFetch<AdminUserSummary>(`/api/admin-users/${id}`, {
        method: "PUT",
        body: JSON.stringify({ isActive }),
      });
      setAdminUsers((prev) => prev.map((u) => (u.id === id ? updated : u)));
      showToast(isActive ? "Account activated" : "Account deactivated");
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to update account status");
    }
  }

  function openDeleteModal(target: AdminUserSummary) {
    setDeleteTarget(target);
    setReassignToId("");
    setConfirmForceDelete(false);
  }

  function closeDeleteModal() {
    setDeleteTarget(null);
    setReassignToId("");
    setConfirmForceDelete(false);
  }

  async function handleDeleteAdmin(options?: { reassignTo?: number; force?: boolean }) {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const params = new URLSearchParams();
      if (options?.reassignTo) params.set("reassign_to", String(options.reassignTo));
      if (options?.force) params.set("force", "true");
      const qs = params.toString();
      await apiClientFetch(`/api/admin-users/${deleteTarget.id}${qs ? `?${qs}` : ""}`, { method: "DELETE" });

      setAdminUsers((prev) => prev.filter((u) => u.id !== deleteTarget.id));
      if (options?.force) {
        setListings((prev) => prev.filter((l) => l.ownerId !== deleteTarget.id));
      } else if (options?.reassignTo) {
        const newOwnerId = options.reassignTo;
        setListings((prev) => prev.map((l) => (l.ownerId === deleteTarget.id ? { ...l, ownerId: newOwnerId } : l)));
      }
      showToast("Admin account deleted");
      closeDeleteModal();
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to delete admin account");
    } finally {
      setDeleting(false);
    }
  }

  async function handleApproveRegistration(id: number) {
    try {
      const updated = await apiClientFetch<AdminUserSummary>(`/api/admin-users/${id}/approve`, { method: "POST" });
      setAdminUsers((prev) => prev.map((u) => (u.id === id ? updated : u)));
      showToast("Registration approved — they can now sign in");
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to approve registration");
    }
  }

  async function handleRejectRegistration(id: number) {
    try {
      const updated = await apiClientFetch<AdminUserSummary>(`/api/admin-users/${id}/reject`, { method: "POST" });
      setAdminUsers((prev) => prev.map((u) => (u.id === id ? updated : u)));
      showToast("Registration rejected");
    } catch (err) {
      showToast(err instanceof ApiError ? err.message : "Failed to reject registration");
    }
  }

  const pending = adminUsers.filter((u) => u.approvalStatus === "pending");
  const rest = adminUsers.filter((u) => u.approvalStatus !== "pending");

  return (
    <div className="max-w-2xl space-y-6">
      {pending.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-bold text-primary-900 dark:text-white">Pending Registrations</h3>
          {pending.map((u) => (
            <div
              key={u.id}
              className="flex flex-wrap items-center justify-between gap-3 rounded-xl bg-amber-50 px-4 py-3 ring-1 ring-amber-200 dark:bg-amber-500/10 dark:ring-amber-500/20"
            >
              <div>
                <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{u.fullName}</p>
                <p className="text-xs text-slate-500 dark:text-slate-400">
                  {u.email} · applied {formatDate(u.createdAt)}
                </p>
              </div>
              <div className="flex gap-2">
                <Button size="sm" variant="primary" onClick={() => handleApproveRegistration(u.id)}>
                  <CheckCircle2 className="h-3.5 w-3.5" /> Approve
                </Button>
                <Button size="sm" variant="outline" onClick={() => handleRejectRegistration(u.id)}>
                  Reject
                </Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2">
        {rest.map((u) => {
          const isSelf = u.id === currentAdminId;
          return (
            <div
              key={u.id}
              className={cn(
                "flex flex-wrap items-center justify-between gap-3 rounded-xl bg-slate-50 px-4 py-3 dark:bg-slate-800",
                (!u.isActive || u.approvalStatus === "rejected") && "opacity-60"
              )}
            >
              <div>
                <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                  {u.fullName} {isSelf && <span className="text-xs font-normal text-slate-400">(you)</span>}
                </p>
                <p className="text-xs text-slate-400">
                  {u.email} · joined {formatDate(u.createdAt)}
                </p>
              </div>
              <div className="flex items-center gap-3">
                {u.approvalStatus === "rejected" && (
                  <Badge tone="danger" className="capitalize">
                    Rejected
                  </Badge>
                )}
                {!u.isActive && u.approvalStatus === "approved" && (
                  <Badge tone="danger" className="capitalize">
                    Deactivated
                  </Badge>
                )}
                <button
                  onClick={() => setViewingAdmin(u)}
                  className="flex items-center gap-1.5 rounded-lg bg-white px-2.5 py-1.5 text-xs font-semibold text-primary-700 ring-1 ring-slate-200 transition-colors hover:bg-primary-50 dark:bg-slate-900 dark:text-primary-300 dark:ring-slate-700 dark:hover:bg-primary-500/10"
                >
                  <Building2 className="h-3.5 w-3.5" />
                  {(listingsByOwner.get(u.id) ?? []).length} {(listingsByOwner.get(u.id) ?? []).length === 1 ? "room" : "rooms"}
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
                <select
                  value={u.role}
                  disabled={isSelf}
                  onChange={(e) => handleChangeAdminRole(u.id, e.target.value as AdminRole)}
                  className="rounded-lg bg-white px-2.5 py-1.5 text-xs font-semibold outline-none ring-1 ring-slate-200 disabled:cursor-not-allowed disabled:opacity-60 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
                >
                  <option value="admin">Admin</option>
                  <option value="super_admin">Super Admin</option>
                </select>
                <Switch checked={u.isActive} onChange={(v) => !isSelf && handleToggleAdminActive(u.id, v)} />
                <button
                  onClick={() => !isSelf && openDeleteModal(u)}
                  disabled={isSelf}
                  title={isSelf ? "You can't delete your own account" : "Delete account"}
                  className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-accent-50 hover:text-accent-600 disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent dark:text-slate-400 dark:hover:bg-accent-500/10"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <form onSubmit={handleCreateAdmin} className="space-y-3 rounded-2xl bg-white p-6 shadow-sm ring-1 ring-slate-100 dark:bg-slate-900 dark:ring-white/10">
        <h3 className="text-sm font-bold text-primary-900 dark:text-white">Add Team Member</h3>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Full Name
            </label>
            <input
              value={adminForm.fullName}
              onChange={(e) => setAdminForm((f) => ({ ...f, fullName: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Email
            </label>
            <input
              type="email"
              value={adminForm.email}
              onChange={(e) => setAdminForm((f) => ({ ...f, email: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Password
            </label>
            <input
              type="password"
              value={adminForm.password}
              onChange={(e) => setAdminForm((f) => ({ ...f, password: e.target.value }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">
              Role
            </label>
            <select
              value={adminForm.role}
              onChange={(e) => setAdminForm((f) => ({ ...f, role: e.target.value as AdminRole }))}
              className="w-full rounded-xl bg-slate-50 px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-800 dark:text-slate-100 dark:ring-slate-700"
            >
              <option value="admin">Admin (lists rooms only)</option>
              <option value="super_admin">Super Admin (full access)</option>
            </select>
          </div>
        </div>
        <Button type="submit">Create Account</Button>
      </form>

      <Modal
        open={viewingAdmin !== null}
        onClose={() => setViewingAdmin(null)}
        title={viewingAdmin ? `${viewingAdmin.fullName}'s Properties` : "Properties"}
      >
        <div className="max-h-[60vh] space-y-2.5 overflow-y-auto pr-1">
          {viewingAdminListings.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-400 dark:text-slate-500">
              This admin hasn&apos;t listed any properties yet.
            </p>
          ) : (
            viewingAdminListings.map((listing) => (
              <div
                key={listing.id}
                className="flex items-center gap-3 rounded-xl bg-slate-50 p-3 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={resolveImageUrl(listing.images[0])} alt={listing.name} className="h-14 w-14 shrink-0 rounded-lg object-cover" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-primary-900 dark:text-white">{listing.name}</p>
                  <p className="truncate text-xs text-slate-500 dark:text-slate-400">{listing.city}</p>
                  <StarRating rating={listing.rating} size={11} reviewCount={listing.reviewCount} />
                </div>
                <div className="flex shrink-0 flex-col items-end gap-1.5">
                  <span className="text-sm font-bold text-primary-800 dark:text-primary-200">
                    {formatCurrency(listing.pricePerNight, listing.currency)}
                  </span>
                  <Badge tone={listingStateTone[listing.state]}>{listingStateLabel[listing.state]}</Badge>
                </div>
              </div>
            ))
          )}
        </div>
      </Modal>

      <Modal open={deleteTarget !== null} onClose={closeDeleteModal} title="Delete Admin Account">
        {deleteTarget && (
          <div className="space-y-4">
            <div className="flex items-start gap-3 rounded-xl bg-accent-50 p-3.5 ring-1 ring-accent-200 dark:bg-accent-500/10 dark:ring-accent-500/20">
              <AlertTriangle className="h-5 w-5 shrink-0 text-accent-600" />
              <p className="text-sm text-accent-700 dark:text-accent-300">
                You&apos;re about to permanently delete <strong>{deleteTarget.fullName}</strong>&apos;s account (
                {deleteTarget.email}). This can&apos;t be undone.
              </p>
            </div>

            {deleteTargetListings.length === 0 ? (
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={closeDeleteModal}>
                  Cancel
                </Button>
                <Button variant="accent" loading={deleting} onClick={() => handleDeleteAdmin()}>
                  Delete Account
                </Button>
              </div>
            ) : (
              <div className="space-y-4">
                <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">
                  This admin owns {deleteTargetListings.length} listing{deleteTargetListings.length === 1 ? "" : "s"}.
                  Choose what to do with {deleteTargetListings.length === 1 ? "it" : "them"} before deleting the account:
                </p>

                <div className="rounded-xl bg-slate-50 p-4 ring-1 ring-slate-100 dark:bg-slate-800 dark:ring-white/10">
                  <p className="text-xs font-bold uppercase tracking-wide text-slate-500 dark:text-slate-400">
                    Option 1 · Reassign (recommended)
                  </p>
                  <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                    Move their listings to another admin, then delete the account. No data is lost.
                  </p>
                  <div className="mt-3 flex gap-2">
                    <select
                      value={reassignToId}
                      onChange={(e) => setReassignToId(e.target.value)}
                      className="flex-1 rounded-xl bg-white px-4 py-2.5 text-sm outline-none ring-1 ring-slate-200 focus:ring-2 focus:ring-primary-400 dark:bg-slate-900 dark:text-slate-100 dark:ring-slate-700"
                    >
                      <option value="">Select an admin...</option>
                      {reassignOptions.map((u) => (
                        <option key={u.id} value={u.id}>
                          {u.fullName} ({u.email})
                        </option>
                      ))}
                    </select>
                    <Button
                      variant="primary"
                      loading={deleting}
                      disabled={!reassignToId}
                      onClick={() => handleDeleteAdmin({ reassignTo: Number(reassignToId) })}
                    >
                      Reassign &amp; Delete
                    </Button>
                  </div>
                </div>

                <div className="rounded-xl bg-accent-50 p-4 ring-1 ring-accent-200 dark:bg-accent-500/10 dark:ring-accent-500/20">
                  <p className="text-xs font-bold uppercase tracking-wide text-accent-700 dark:text-accent-300">
                    Option 2 · Delete everything
                  </p>
                  <p className="mt-1 text-xs text-accent-700 dark:text-accent-300">
                    Permanently delete the account along with all {deleteTargetListings.length} listing
                    {deleteTargetListings.length === 1 ? "" : "s"}, and any bookings, payments and reviews tied to
                    {deleteTargetListings.length === 1 ? " it" : " them"}.
                  </p>
                  <label className="mt-3 flex items-center gap-2 text-xs font-medium text-accent-700 dark:text-accent-300">
                    <input
                      type="checkbox"
                      checked={confirmForceDelete}
                      onChange={(e) => setConfirmForceDelete(e.target.checked)}
                      className="h-4 w-4 rounded accent-accent-600"
                    />
                    I understand this permanently deletes their listings and booking history
                  </label>
                  <div className="mt-3 flex justify-end">
                    <Button
                      variant="accent"
                      loading={deleting}
                      disabled={!confirmForceDelete}
                      onClick={() => handleDeleteAdmin({ force: true })}
                    >
                      Delete Everything
                    </Button>
                  </div>
                </div>

                <div className="flex justify-end">
                  <Button variant="outline" onClick={closeDeleteModal}>
                    Cancel
                  </Button>
                </div>
              </div>
            )}
          </div>
        )}
      </Modal>

      {toast && (
        <div className="animate-fade-up fixed bottom-6 right-6 z-[300] flex items-center gap-2 rounded-xl bg-primary-900 px-4 py-3 text-sm font-medium text-white shadow-2xl">
          <CheckCircle2 className="h-4 w-4 text-emerald-400" /> {toast}
        </div>
      )}
    </div>
  );
}
