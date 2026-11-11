import { Suspense } from "react";
import { Integrations } from "@/components/integrations";

export default function IntegrationsPage() {
  return (
    <Suspense>
      <Integrations />
    </Suspense>
  );
}
