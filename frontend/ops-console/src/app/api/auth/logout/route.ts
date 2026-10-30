import { NextRequest, NextResponse } from "next/server";

import { SESSION_COOKIE } from "@/lib/server/oidc";

export async function POST(request: NextRequest) {
  const response = NextResponse.redirect(new URL("/sign-in?signed_out=1", request.nextUrl), 303);
  response.cookies.delete(SESSION_COOKIE);
  return response;
}
