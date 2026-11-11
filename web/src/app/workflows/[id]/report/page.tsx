import { Suspense } from "react";
import { Report } from "@/components/report";

async function ReportRoute({ params }: { params: PageProps<"/workflows/[id]/report">["params"] }) {
  const { id } = await params;
  return <Report id={id} />;
}

export default function ReportPage({ params }: PageProps<"/workflows/[id]/report">) {
  return (
    <Suspense>
      <ReportRoute params={params} />
    </Suspense>
  );
}
