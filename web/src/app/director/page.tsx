import { Suspense } from "react";
import { Director } from "@/components/director";

export default function DirectorPage() {
  return (
    <Suspense>
      <Director />
    </Suspense>
  );
}
