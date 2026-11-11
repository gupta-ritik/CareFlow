import { Suspense } from "react";
import { Setup } from "@/components/login";

export default function SetupPage() {
  return (
    <Suspense>
      <Setup />
    </Suspense>
  );
}
