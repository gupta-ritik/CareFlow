"use client";

import { ArrowLeft, DoorOpen, FileText, Link2, RefreshCw, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { ClearanceRail } from "./clearance-rail";
import { Timeline } from "./timeline";
import { EvidenceRef, Panel, RiskTag, Skeleton, TaskStatusText, WorkflowPill } from "./ui";
import { ACTION_LABEL, DEPT_LABEL, KIND_LABEL, clock, duration, minutesSince, since } from "@/lib/format";
import { useActions, useAudit, useCloseDischarge, useRecheck, useWorkflow } from "@/lib/queries";
import { useCan } from "@/lib/session";

export function Workspace({ id }: { id: string }) {
  const { data: wf, isPending, error } = useWorkflow(id);
  const { data: actions } = useActions();
  const { data: events } = useAudit(id);
  const recheck = useRecheck();
  const close = useCloseDischarge();
  const canRecheck = useCan("workflow:start");
  const canClose = useCan("discharge:close");

  if (isPending) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-10 w-72" />
        <Skeleton className="h-40" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  if (!wf) {
    return (
      <Panel className="p-6">
        <p className="font-semibold text-ink">Workflow {id} couldn&apos;t be loaded.</p>
        <p className="mt-1 text-sm text-slate">{error?.message ?? "It may have been closed."} Return to the board to see open workflows.</p>
        <Link href="/" className="mt-4 inline-flex text-sm font-semibold text-harbor">
          Back to discharge board
        </Link>
      </Panel>
    );
  }

  const wfActions = (actions ?? []).filter((a) => a.workflowId === wf.id);
  const evidenceById = new Map(wf.evidence.map((e) => [e.id, e]));

  return (
    <div className="flex flex-col gap-5">
      <div>
        <Link href="/" className="inline-flex items-center gap-1.5 text-sm font-semibold text-slate hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Discharge board
        </Link>
        <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[1.75rem] font-bold leading-tight tracking-[-0.02em] text-ink sm:text-[2rem]">{wf.patient.displayName}</h1>
            <WorkflowPill status={wf.status} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href={`/workflows/${wf.id}/report`}
              className="inline-flex items-center gap-2 rounded-xl border border-line bg-surface-2 px-3.5 py-2 text-sm font-semibold text-ink hover:bg-hover"
            >
              <FileText className="size-4" aria-hidden /> Clearance report
            </Link>
            {canRecheck && !["discharged", "cleared"].includes(wf.status) && (
              <button
                type="button"
                onClick={() => recheck.mutate(wf.id)}
                disabled={recheck.isPending || wf.status === "awaiting_approval"}
                title={wf.status === "awaiting_approval" ? "Decide the pending approval first" : "Re-read every department now"}
                className="inline-flex items-center gap-2 rounded-xl border border-line bg-surface-2 px-3.5 py-2 text-sm font-semibold text-ink hover:bg-hover disabled:opacity-50"
              >
                <RefreshCw className={recheck.isPending ? "size-4 animate-spin" : "size-4"} aria-hidden />
                {recheck.isPending ? "Re-checking" : "Re-check now"}
              </button>
            )}
            {canClose && wf.status === "cleared" && (
              <button
                type="button"
                onClick={() => close.mutate(wf.id)}
                disabled={close.isPending}
                className="inline-flex items-center gap-2 rounded-xl bg-tide px-3.5 py-2 text-sm font-semibold text-white hover:bg-navy disabled:opacity-60"
              >
                <DoorOpen className="size-4" aria-hidden />
                {close.isPending ? "Closing" : "Patient has left: close discharge"}
              </button>
            )}
          </div>
        </div>
        <p className="tnum mt-1 text-sm text-slate">
          {wf.patient.ward}, {wf.patient.bed}. {wf.patient.mrn}. Workflow {wf.id}
        </p>
        {wf.summary && <p className="mt-3 max-w-[72ch] text-[0.95rem] leading-relaxed text-ink/85">{wf.summary}</p>}
        {(recheck.error || close.error) && <p className="mt-2 text-sm text-coral">{(recheck.error || close.error)!.message}</p>}
      </div>

      <Panel strong className="p-4 sm:p-5">
        <ClearanceRail tasks={wf.tasks} authorizedBy={wf.authorizedBy} authorizedAt={wf.authorizedAt} />
      </Panel>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <Panel className="p-4 sm:p-5">
            <h2 className="text-lg font-bold text-ink">Blockers</h2>
            {wf.blockers.length === 0 ? (
              <p className="mt-2 text-sm text-slate">
                {wf.status === "cleared"
                  ? "None. Every department was re-checked against its own system."
                  : "None found on the last check. CareFlow re-checks whenever a department updates its work."}
              </p>
            ) : (
              <ul className="mt-3 flex flex-col gap-3">
                {wf.blockers.map((b) => (
                  <li key={b.id} className="rounded-2xl border border-amber/40 bg-amber-soft/50 p-4">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <p className="font-semibold text-ink">
                        {DEPT_LABEL[b.department]}
                        <span className="font-normal text-slate"> / {KIND_LABEL[b.kind] ?? b.kind}</span>
                      </p>
                      <p className="tnum text-xs text-slate">Open for {duration(minutesSince(b.since))}</p>
                    </div>
                    <p className="mt-1 text-[0.95rem] leading-snug text-ink/90">{b.summary}</p>
                    {b.waitingOn && (
                      <p className="mt-2 inline-flex items-center gap-1.5 text-sm text-slate">
                        <Link2 className="size-4" aria-hidden /> Depends on {DEPT_LABEL[b.waitingOn].toLowerCase()} clearing first
                      </p>
                    )}

                    <div className="mt-3 overflow-hidden rounded-xl border border-line bg-surface-2">
                      <p className="border-b border-line px-3 py-2 text-xs font-semibold text-slate">Evidence</p>
                      <ul className="divide-y divide-line">
                        {b.evidenceIds.map((eid) => {
                          const ev = evidenceById.get(eid);
                          if (!ev) return null;
                          return (
                            <li key={eid} className="grid gap-1 px-3 py-2.5 text-sm sm:grid-cols-[auto_minmax(0,1fr)] sm:gap-3">
                              <EvidenceRef id={ev.id} />
                              <div className="min-w-0">
                                <p className="text-ink">{ev.observed}</p>
                                <p className="mt-0.5 truncate text-xs text-faint">
                                  {ev.source}: <code className="text-slate">{ev.query}</code>, read {since(ev.observedAt)}
                                </p>
                              </div>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {wf.evidence.some((e) => e.untrusted) && (
              <p className="mt-3 flex items-start gap-2 rounded-xl bg-coral-soft/70 px-3 py-2 text-sm text-ink">
                <ShieldAlert className="mt-0.5 size-4 shrink-0 text-coral" aria-hidden />
                A document for this patient contains text that tries to instruct the agent. It was treated as data and can&apos;t trigger any action.
              </p>
            )}
          </Panel>

          <Panel className="p-4 sm:p-5">
            <h2 className="text-lg font-bold text-ink">Department tasks</h2>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[600px] text-left text-sm">
                <thead className="text-xs text-faint">
                  <tr>
                    <th className="pb-2 pr-4 font-semibold">Department</th>
                    <th className="pb-2 pr-4 font-semibold">Task</th>
                    <th className="pb-2 pr-4 font-semibold">Owner</th>
                    <th className="pb-2 pr-4 font-semibold">Status</th>
                    <th className="pb-2 text-right font-semibold">Source updated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {wf.tasks.map((t) => (
                    <tr key={t.id}>
                      <td className="py-2.5 pr-4 font-semibold text-ink">{DEPT_LABEL[t.department]}</td>
                      <td className="py-2.5 pr-4 text-slate">{t.title}</td>
                      <td className="py-2.5 pr-4 text-slate">{t.owner}</td>
                      <td className="whitespace-nowrap py-2.5 pr-4">
                        <TaskStatusText status={t.status} />
                      </td>
                      <td className="tnum py-2.5 text-right text-slate">{since(t.updatedAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>

        </div>

        <div className="flex min-w-0 flex-col gap-5">
          <Panel className="p-4 sm:p-5">
            <h2 className="text-lg font-bold text-ink">Actions</h2>
            {wfActions.length === 0 ? (
              <p className="mt-2 text-sm text-slate">No actions proposed for this workflow.</p>
            ) : (
              <ul className="mt-3 flex flex-col gap-3">
                {wfActions.map((a) => (
                  <li key={a.id} className="rounded-2xl bg-surface-2 p-4">
                    <div className="flex items-center justify-between gap-2">
                      <code className="text-xs font-semibold text-harbor">{a.action}</code>
                      <RiskTag risk={a.risk} />
                    </div>
                    <p className="mt-2 text-sm leading-snug text-ink">{a.description}</p>
                    <p className="mt-2 text-xs text-slate">
                      {ACTION_LABEL[a.status]}
                      {a.sopRefs.length > 0 && <span className="text-faint">. Per {a.sopRefs[0]}</span>}
                    </p>
                    {a.status === "pending" && (
                      <Link
                        href={`/approvals?id=${a.id}`}
                        className="mt-3 inline-flex rounded-xl bg-ink px-3.5 py-2 text-sm font-semibold text-white hover:bg-ink/90"
                      >
                        Review in Approvals
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel className="p-4 sm:p-5">
            <h2 className="text-lg font-bold text-ink">Administrative context</h2>
            <dl className="mt-3 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
              <dt className="text-slate">Payer</dt>
              <dd className="text-ink">{wf.patient.payer}</dd>
              <dt className="text-slate">Attending</dt>
              <dd className="text-ink">{wf.patient.attending}</dd>
              <dt className="text-slate">Admitted</dt>
              <dd className="tnum text-ink">{duration(minutesSince(wf.patient.admittedAt))} ago</dd>
              <dt className="text-slate">Authorized</dt>
              <dd className="tnum text-ink">
                {clock(wf.authorizedAt)} by {wf.authorizedBy}
              </dd>
            </dl>
            <p className="mt-3 text-xs leading-snug text-faint">Clinical details are not shown here. CareFlow only reads what it needs to coordinate clearance.</p>
          </Panel>

          <Panel className="p-4 sm:p-5">
            <h2 className="mb-3 text-lg font-bold text-ink">History</h2>
            {events ? <Timeline events={events} /> : <Skeleton className="h-32" />}
          </Panel>
        </div>
      </div>
    </div>
  );
}
