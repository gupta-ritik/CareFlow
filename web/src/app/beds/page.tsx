import { Suspense } from "react";
import { Beds } from "@/components/beds";

export default function BedsPage() {
  return (
    <Suspense>
      <Beds />
    </Suspense>
  );
}
