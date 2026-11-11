"use client";

import clsx from "clsx";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { MiniRail } from "./clearance-rail";
import { PageHeader, Panel, Skeleton, WorkflowPill } from "./ui";
import { DEPT_LABEL, duration, minutesSince } from "@/lib/format";
import { useWorkflows } from "@/lib/queries";
import type { Workflow, WorkflowStatus } from "@/lib/types";

type Filter = "all" | WorkflowStatus;

const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "blocked", label: "Blocked" },
  { key: "awaiting_approval", label: "Needs approval" },
  { key: "in_progress", label: "Clearing" },
  { key: "cleared", label: "Ready" },
];

// Blocked and needs-approval first, then longest wait.
const ORDER: Record<WorkflowStatus, number> = { blocked: 0, awaiting_approval: 1, in_progress: 2, cleared: 3, discharged: 4 };

function Summary({ workflows }: { workflows: Workflow[] }) {
  const open = workflows.filter((w) => w.status !== "cleared" && w.status !== "discharged");
  const blocked = workflows.filter((w) => w.status === "blocked").length;
  const approval = workflows.filter((w) => w.status === "awaiting_approval").length;
  const waits = open.map((w) => minutesSince(w.authorizedAt)).sort((a, b) => a - b);
  const median = waits.length ? waits[Math.floor(waits.length / 2)] : 0;

  return (
    <p className="tnum">
      <strong className="text-ink">{open.length} patients</strong> are clinically cleared and waiting on administration.{" "}
      <strong className="text-[#8a560c]">{blocked} blocked</strong>,{" "}
      <strong className="text-[#4b3d93]">{approval} need your approval</strong>, median wait{" "}
      <strong className="text-ink">{duration(median)}</strong>.
    </p>
  );
}

function Row({ wf }: { wf: Workflow }) {
  const wait = minutesSince(wf.authorizedAt);
  const top = wf.blockers[0];

  return (
    <li>
      <Link
        href={`/workflows/${wf.id}`}
        className="group grid grid-cols-1 gap-3 rounded-2xl px-4 py-4 transition-colors hover:bg-white/55 md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.4fr)_13rem] md:items-center md:gap-6"
      >
        <div className="min-w-0">
          <p className="truncate font-semibold text-ink">{wf.patient.displayName}</p>
          <p className="tnum truncate text-sm text-slate">
            {wf.patient.ward}, {wf.patient.bed}
            <span className="text-faint"> / {wf.patient.mrn}</span>
          </p>
        </div>

        <div>
          <MiniRail tasks={wf.tasks} />
          <p className="tnum mt-1.5 text-xs text-slate">
            {wf.tasks.filter((t) => t.status === "cleared").length} of {wf.tasks.length} cleared
          </p>
        </div>

        <p className="min-w-0 text-sm leading-snug text-slate">
          {top ? (
            <>
              <span className="font-semibold text-ink">{DEPT_LABEL[top.department]}:</span> {top.summary}
              {wf.blockers.length > 1 && <span className="text-faint"> +{wf.blockers.length - 1} more</span>}
            </>
          ) : wf.status === "cleared" ? (
            "All departments verified."
          ) : (
            "No blockers found. Departments are working."
          )}
        </p>

        <div className="flex items-center justify-between gap-4 md:justify-end">
          <div className="flex flex-col items-start gap-1 md:items-end">
            <WorkflowPill status={wf.status} />
            <span className="tnum text-xs text-slate">{duration(wait)} since authorization</span>
          </div>
          <ChevronRight className="size-5 text-faint transition-transform group-hover:translate-x-0.5" aria-hidden />
        </div>
      </Link>
    </li>
  );
}

export function Board() {
  const { data, isPending, error } = useWorkflows();
  const [filter, setFilter] = useState<Filter>("all");

  const rows = useMemo(() => {
    const list = (data ?? []).filter((w) => filter === "all" || w.status === filter);
    return list.sort((a, b) => ORDER[a.status] - ORDER[b.status] || a.authorizedAt.localeCompare(b.authorizedAt));
  }, [data, filter]);

  return (
    <>
      <PageHeader title="Discharge board">
        {data ? <Summary workflows={data} /> : <Skeleton className="h-5 w-3/4" />}
      </PageHeader>

      <Panel className="p-2 sm:p-3">
        <div className="flex flex-wrap items-center justify-between gap-3 px-2 pb-2 pt-1">
          <div role="tablist" aria-label="Filter workflows" className="flex flex-wrap gap-1 rounded-full bg-ink/[0.05] p-1">
            {FILTERS.map((f) => {
              const count = f.key === "all" ? data?.length : data?.filter((w) => w.status === f.key).length;
              return (
                <button
                  key={f.key}
                  role="tab"
                  aria-selected={filter === f.key}
                  onClick={() => setFilter(f.key)}
                  className={clsx(
                    "tnum rounded-full px-3 py-1.5 text-sm font-semibold transition-colors",
                    filter === f.key ? "bg-white text-ink shadow-sm" : "text-slate hover:text-ink",
                  )}
                >
                  {f.label}
                  {count !== undefined && <span className="ml-1.5 text-faint">{count}</span>}
                </button>
              );
            })}
          </div>
          <p className="hidden text-xs text-slate md:block">Each rail segment is one department, in dependency order. Updates every few seconds.</p>
        </div>

        <div className="hidden grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.4fr)_13rem] gap-6 border-t border-white/70 px-4 pb-1 pt-3 text-xs font-semibold text-faint md:grid">
          <span>Patient</span>
          <span>Clearance</span>
          <span>What is holding it up</span>
          <span className="pr-9 text-right">Status</span>
        </div>

        {error ? (
          <p className="p-4 text-sm text-coral">Couldn&apos;t load workflows. {error.message}</p>
        ) : isPending ? (
          <div className="flex flex-col gap-2 p-2">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-16" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          filter === "all" ? (
            <div className="px-4 py-10 text-center">
              <p className="font-semibold text-ink">No patients are waiting for discharge clearance.</p>
              <p className="mt-1 text-sm text-slate">
                A patient appears here once a clinician authorizes their discharge on the{" "}
                <Link href="/patients" className="font-semibold text-harbor hover:underline">
                  Patients
                </Link>{" "}
                page.
              </p>
            </div>
          ) : (
            <p className="p-6 text-sm text-slate">No workflows match this filter. Choose All to see every patient.</p>
          )
        ) : (
          <ul className="divide-y divide-white/70">
            {rows.map((wf) => (
              <Row key={wf.id} wf={wf} />
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}
