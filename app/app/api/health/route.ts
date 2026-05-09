import { NextResponse } from "next/server";

/**
 * Minimal health endpoint. The home page pings this to verify both client and
 * server halves of Next.js are working.
 *
 * When the FastAPI backend exists, this will also forward a ping there and
 * return its status, so a single check covers the whole stack.
 */
export async function GET() {
  return NextResponse.json({
    ok: true,
    ts: new Date().toISOString(),
  });
}
