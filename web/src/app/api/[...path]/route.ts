import type { NextRequest } from "next/server";

import { proxyToApi } from "@/lib/api/proxy";

/** `/api/*` → the read API (`BPS_API_INTERNAL_URL`, runtime); see `lib/api/proxy.ts`. */
export async function GET(request: NextRequest, ctx: RouteContext<"/api/[...path]">) {
  const { path } = await ctx.params;
  return proxyToApi(request, path);
}

export const HEAD = GET;
