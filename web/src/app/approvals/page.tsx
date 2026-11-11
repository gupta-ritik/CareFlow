import { Suspense } from "react";
import { Approvals } from "@/components/approvals";

export default function ApprovalsPage() {
  return (
    <Suspense>
      <Approvals />
    </Suspense>
  );
}
