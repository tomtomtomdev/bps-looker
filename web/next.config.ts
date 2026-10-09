import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The Docker image (web/Dockerfile) builds with NEXT_STANDALONE=1: `.next/standalone` holds a
  // minimal `server.js` + only the traced node_modules. Off by default so `next start` (local
  // runs, the mocked e2e) keeps working without the "use server.js instead" warning.
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
