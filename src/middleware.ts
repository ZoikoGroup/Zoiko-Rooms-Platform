import { NextRequest, NextResponse } from "next/server";

const ADMIN_AUTH_COOKIE = "zoiko_admin_token";
const USER_AUTH_COOKIE = "zoiko_user_token";

// The USER area signs in at its own pages, which must stay reachable while signed out.
const PUBLIC_ACCOUNT_PATHS = [
  "/account/login",
  "/account/register",
  "/account/forgot-password",
  "/account/reset-password",
];

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // Two independent sessions live side by side: /account is gated on the user cookie,
  // everything else in the matcher stays gated on the admin cookie exactly as before.
  // Neither cookie can satisfy the other area's guard.
  if (pathname === "/account" || pathname.startsWith("/account/")) {
    if (PUBLIC_ACCOUNT_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`))) {
      return NextResponse.next();
    }
    if (!request.cookies.get(USER_AUTH_COOKIE)) {
      return NextResponse.redirect(new URL("/account/login", request.url));
    }
    return NextResponse.next();
  }

  if (!request.cookies.get(ADMIN_AUTH_COOKIE)) {
    return NextResponse.redirect(new URL("/login", request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: [
    "/",
    "/bookings/:path*",
    "/guests/:path*",
    "/properties/:path*",
    "/payments/:path*",
    "/reviews/:path*",
    "/team/:path*",
    "/settings/:path*",
    "/trust-safety/:path*",
    "/finance/:path*",
    "/leasing/:path*",
    "/occupancy/:path*",
    "/account",
    "/account/:path*",
  ],
};
