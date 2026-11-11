"use client";

import clsx from "clsx";
import { ChevronRight, LayoutList, RefreshCw, Search } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { MiniRail } from "./clearance-rail";
import { Button, EmptyState, PageHeader, Panel, Skeleton, Tabs, WorkflowPill, inputClass, selectClass } from "./ui";
import { DEPT_LABEL, duration, minutesSince } from "@/lib/format";
import { useWorkflows } from "@/lib/queries";
import type { Department, Workflow, WorkflowStatus } from "@/lib/types";

type Sort = "priority" | "longest" | "newest";
const DEPTS = Object.keys(DEPT_LABEL) as Department[];

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
      <strong className="text-amber-ink">{blocked} blocked</strong>,{" "}
      <strong className="text-violet">{approval} need your approval</strong>, median wait{" "}
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
        className="group grid grid-cols-1 gap-3 px-4 py-4 transition-colors sm:px-6 hover:bg-hover md:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.4fr)_13rem] md:items-center md:gap-6"
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
  const { data, isPending, error, isFetching, dataUpdatedAt } = useWorkflows();
  const qc = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [dept, setDept] = useState<"" | Department>("");
  const [sort, setSort] = useState<Sort>("priority");

  const rows = useMemo(() => {
    const term = q.trim().toLowerCase();
    const list = (data ?? []).filter(
      (w) =>
        (filter === "all" || w.status === filter) &&
        (!dept || w.blockers.some((b) => b.department === dept) || w.tasks.some((t) => t.department === dept && t.status !== "cleared")) &&
        (!term || [w.id, w.patient.displayName, w.patient.mrn, w.patient.ward, w.patient.bed].some((s) => s?.toLowerCase().includes(term))),
    );
    return list.sort((a, b) =>
      sort === "longest"
        ? a.authorizedAt.localeCompare(b.authorizedAt)
        : sort === "newest"
          ? b.authorizedAt.localeCompare(a.authorizedAt)
          : ORDER[a.status] - ORDER[b.status] || a.authorizedAt.localeCompare(b.authorizedAt),
    );
  }, [data, filter, q, dept, sort]);
  const filtered = Boolean(q.trim() || dept);
  const clear = () => {
    setFilter("all");
    setQ("");
    setDept("");
  };
  const refreshing = isFetching && !isPending;

  return (
    <>
      <PageHeader
        title="Discharge board"
        actions={
          <Button variant="secondary" onClick={() => qc.invalidateQueries({ queryKey: ["workflows"] })} loading={refreshing}>
            {!refreshing && <RefreshCw className="size-4" aria-hidden />} Refresh
          </Button>
        }
      >
        {data ? <Summary workflows={data} /> : <Skeleton className="h-5 w-3/4" />}
      </PageHeader>

      <Panel>
        <div className="flex flex-col gap-3 border-b border-line p-3 sm:p-4 2xl:flex-row 2xl:items-center 2xl:justify-between">
          <Tabs<Filter>
            wrap
            label="Filter by status"
            value={filter}
            onChange={setFilter}
            options={FILTERS.map((f) => ({ value: f.key, label: f.label, count: f.key === "all" ? data?.length : data?.filter((w) => w.status === f.key).length }))}
          />
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative sm:w-64">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
              <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Patient, MRN, ward or ID" aria-label="Search the board" className={clsx(inputClass, "pl-9")} />
            </div>
            <select value={dept} onChange={(e) => setDept(e.target.value as "" | Department)} aria-label="Department" className={clsx(selectClass, "sm:w-48")}>
              <option value="">All departments</option>
              {DEPTS.map((d) => (
                <option key={d} value={d}>
                  Waiting on {DEPT_LABEL[d]}
                </option>
              ))}
            </select>
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort" className={clsx(selectClass, "sm:w-48")}>
              <option value="priority">Sort: needs attention</option>
              <option value="longest">Sort: longest wait</option>
              <option value="newest">Sort: newest</option>
            </select>
          </div>
        </div>

        <div className="hidden grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.4fr)_13rem] gap-6 bg-surface-2 px-6 py-2.5 text-[11px] font-semibold uppercase tracking-[0.06em] text-faint md:grid">
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
          filter === "all" && !filtered ? (
            <EmptyState icon={LayoutList} title="No patients are waiting for discharge clearance">
              A patient appears here once a clinician authorizes their discharge on the{" "}
              <Link href="/patients" className="font-semibold text-tide-deep hover:underline">
                Patients
              </Link>{" "}
              page.
            </EmptyState>
          ) : (
            <EmptyState
              icon={Search}
              title="No patients match these filters"
              action={
                <Button variant="secondary" onClick={clear}>
                  Clear filters
                </Button>
              }
            />
          )
        ) : (
          <ul className="divide-y divide-line">
            {rows.map((wf) => (
              <Row key={wf.id} wf={wf} />
            ))}
          </ul>
        )}
        <p className="border-t border-line px-6 py-2.5 text-xs text-faint">
          Each rail segment is one department, in dependency order. Updates every few seconds
          {dataUpdatedAt ? `; last at ${new Date(dataUpdatedAt).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}` : ""}.
        </p>
      </Panel>
    </>
  );
}
