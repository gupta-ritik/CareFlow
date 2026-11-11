import { Suspense } from "react";
import { Queue } from "@/components/queue";

export default function QueuePage() {
  return (
    <Suspense>
      <Queue />
    </Suspense>
  );
}
