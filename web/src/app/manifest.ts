import type { MetadataRoute } from "next";

// Makes CareFlow installable ("Install app" / "Add to Home Screen"): it then opens full screen from its own icon.
export default function manifest(): MetadataRoute.Manifest {
  return {
    id: "/",
    name: "CareFlow",
    short_name: "CareFlow",
    description: "Administrative discharge clearance, coordinated across departments.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "any",
    background_color: "#eaf1f4",
    theme_color: "#0f2236",
    categories: ["medical", "productivity"],
    icons: [
      {
        src: "/icon-192.png",
        sizes: "192x192",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "any",
      },
      {
        src: "/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
    shortcuts: [
      {
        name: "Approvals",
        url: "/approvals",
        icons: [{ src: "/icon-192.png", sizes: "192x192" }],
      },
      {
        name: "Department queues",
        url: "/queue",
        icons: [{ src: "/icon-192.png", sizes: "192x192" }],
      },
      {
        name: "Discharge board",
        url: "/",
        icons: [{ src: "/icon-192.png", sizes: "192x192" }],
      },
    ],
  };
}
