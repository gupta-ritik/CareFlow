import { Suspense } from "react";
import { Workspace } from "@/components/workspace";

async function WorkspaceRoute({ params }: { params: PageProps<"/workflows/[id]">["params"] }) {
  const { id } = await params;
  return <Workspace id={id} />;
}

export default function WorkflowPage({ params }: PageProps<"/workflows/[id]">) {
  return (
    <Suspense>
      <WorkspaceRoute params={params} />
    </Suspense>
  );
}
