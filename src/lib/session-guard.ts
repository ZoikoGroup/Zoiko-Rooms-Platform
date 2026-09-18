"use client";

/**
 * Detects when this browser tab's session identity has been silently
 * replaced by a login/logout that happened in another tab.
 *
 * Root cause: `zoiko_user_token` / `zoiko_admin_token` are single-value
 * cookies scoped to the whole browser profile, never to an individual tab --
 * there is no cookie attribute that changes this. So if User A is browsing in
 * Tab 1 and User B logs in on Tab 2 (same browser), Tab 1's next request
 * silently starts authenticating as User B. This module doesn't (and can't)
 * make cookies tab-scoped; it only detects the swap so the affected tab can
 * stop rendering stale/wrong-identity data and prompt a reload instead of
 * continuing silently.
 */

const BROADCAST_PREFIX = "zoiko:session-changed:";
const TAB_IDENTITY_PREFIX = "zoiko:tab-identity:";

export type SessionKind = "user" | "admin";

function broadcastKey(kind: SessionKind): string {
  return `${BROADCAST_PREFIX}${kind}`;
}

function tabIdentityKey(kind: SessionKind): string {
  return `${TAB_IDENTITY_PREFIX}${kind}`;
}

/**
 * Call right after a successful login or logout for `kind` so every other
 * open tab can notice the browser-wide cookie identity changed under it.
 * `identity` is the new user/admin id, or null on logout.
 */
export function broadcastSessionChange(kind: SessionKind, identity: number | string | null): void {
  try {
    localStorage.setItem(broadcastKey(kind), JSON.stringify({ identity, ts: Date.now() }));
  } catch {
    // localStorage can throw (private browsing, storage disabled/full) -- this
    // is a best-effort UX improvement, never something to fail login/logout over.
  }
}

/**
 * Records which identity *this tab* currently believes it is, using
 * sessionStorage (genuinely per-tab, unlike localStorage/cookies) -- and
 * reports whether that identity just changed from what this tab previously
 * recorded.
 *
 * This covers the reload case specifically: sessionStorage survives a reload
 * of the same tab, so if Tab 1 loaded as User A, User B then logs in on Tab 2,
 * and Tab 1 is reloaded (re-sending the now-shared cookie), the fresh
 * `/api/users/me` fetch on that reload resolves to User B -- but the
 * previously-recorded "A" is still sitting in sessionStorage from before the
 * reload, so comparing against it (instead of blindly overwriting) is what
 * catches the swap even though nothing was "live" to observe a storage event.
 */
export function claimTabIdentity(kind: SessionKind, identity: number | string | null): { changed: boolean } {
  if (identity == null) return { changed: false };
  let previous: string | null = null;
  try {
    previous = sessionStorage.getItem(tabIdentityKey(kind));
  } catch {
    previous = null;
  }
  try {
    sessionStorage.setItem(tabIdentityKey(kind), String(identity));
  } catch {
    // ignore -- same best-effort reasoning as above
  }
  return { changed: previous !== null && previous !== String(identity) };
}

/**
 * Returns true once a `storage` event shows another tab logged in/out as a
 * different identity than the one this tab last recorded via
 * claimTabIdentity. The caller should stop rendering session-scoped data
 * and prompt the user to reload. Covers the *live* case: this tab stays
 * mounted (no reload) while another tab logs in/out.
 */
export function watchForForeignSessionChange(kind: SessionKind, onChanged: () => void): () => void {
  function handleStorage(event: StorageEvent) {
    if (event.key !== broadcastKey(kind) || !event.newValue) return;
    let parsed: { identity: number | string | null };
    try {
      parsed = JSON.parse(event.newValue);
    } catch {
      return;
    }
    let mine: string | null = null;
    try {
      mine = sessionStorage.getItem(tabIdentityKey(kind));
    } catch {
      mine = null;
    }
    // Only fire if this tab already had a known identity and it doesn't match
    // the one that just logged in/out elsewhere -- a tab with no prior
    // identity yet (still loading) has nothing to be silently swapped away from.
    if (mine && String(parsed.identity) !== mine) {
      onChanged();
    }
  }
  window.addEventListener("storage", handleStorage);
  return () => window.removeEventListener("storage", handleStorage);
}
