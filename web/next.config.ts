import type { NextConfig } from "next";

// Optional same-origin proxy (for Vercel): set CAREFLOW_API_ORIGIN=https://careflow-api.onrender.com and
// NEXT_PUBLIC_API_BASE=/api at build time. The browser then calls /api on the web domain and the host forwards it, so
// preview deployments work without listing each preview URL in CAREFLOW_CORS_ORIGINS.
const apiOrigin = process.env.CAREFLOW_API_ORIGIN?.replace(/\/+$/, "");

const nextConfig: NextConfig = {
  /* config options here */
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
  async rewrites() {
    return apiOrigin ? [{ source: "/api/:path*", destination: `${apiOrigin}/api/:path*` }] : [];
  },
};

export default nextConfig;
