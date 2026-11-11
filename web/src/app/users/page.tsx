import { Suspense } from "react";
import { Users } from "@/components/users";

export default function UsersPage() {
  return (
    <Suspense>
      <Users />
    </Suspense>
  );
}
