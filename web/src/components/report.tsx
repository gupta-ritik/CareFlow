"use client";

import { ArrowLeft, Printer } from "lucide-react";
import Link from "next/link";
import { ACTION_SHORT, DEPT_LABEL, KIND_LABEL, RISK_LABEL, TASK_LABEL, WORKFLOW_LABEL, duration } from "@/lib/format";
import { useReport } from "@/lib/queries";

const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "Not yet";
const mins = (m: number | null) => (m === null ? "n/a" : duration(m));

/** A printable discharge clearance report. Opens on its own page so the browser can print or save it as PDF. */
export function Report({ id }: { id: string }) {
  const { data: r, isPending, error } = useReport(id);

  if (isPending) return <p className="p-8 text-sm text-slate">Preparing the report.</p>;
  if (error || !r) return <p className="p-8 text-sm text-coral">Couldn&apos;t load the report. {error?.message}</p>;

  const slowest = [...r.departments].filter((d) => d.minutesToClear !== null).sort((a, b) => b.minutesToClear! - a.minutesToClear!)[0];

  return (
    <div className="report mx-auto max-w-[820px] p-4 sm:p-8">
      <div className="no-print mb-6 flex flex-wrap items-center justify-between gap-3">
        <Link href={`/workflows/${r.workflowId}`} className="inline-flex items-center gap-1.5 text-sm font-semibold text-slate hover:text-ink">
          <ArrowLeft className="size-4" aria-hidden /> Back to workflow
        </Link>
        <button type="button" onClick={() => window.print()} className="inline-flex items-center gap-2 rounded-xl bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-ink/90">
          <Printer className="size-4" aria-hidden /> Print or save as PDF
        </button>
      </div>

      <article className="rounded-2xl bg-white p-6 text-ink shadow-[0_12px_32px_-12px_rgba(22,60,98,.18)] sm:p-10 print:p-0 print:shadow-none">
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-ink/10 pb-5">
          <div>
            <p className="text-sm font-semibold text-slate">{r.hospital}</p>
            <h1 className="mt-1 text-2xl font-bold tracking-[-0.02em]">Discharge clearance report</h1>
            <p className="tnum mt-1 text-sm text-slate">
              Workflow {r.workflowId}. {WORKFLOW_LABEL[r.status]}.
            </p>
          </div>
          <div className="text-right text-xs text-slate">
            <p>Generated {when(r.generatedAt)}</p>
            <p>by {r.generatedBy}</p>
          </div>
        </header>

        <section className="grid gap-x-8 gap-y-2 border-b border-ink/10 py-5 text-sm sm:grid-cols-2">
          <dl className="grid grid-cols-[8rem_minmax(0,1fr)] gap-y-1.5">
            <dt className="text-slate">Patient</dt>
            <dd className="font-semibold">{r.patient.displayName}</dd>
            <dt className="text-slate">MRN</dt>
            <dd className="tnum">{r.patient.mrn}</dd>
            <dt className="text-slate">Ward / bed</dt>
            <dd>
              {r.patient.ward}
              {r.patient.bed && `, ${r.patient.bed}`}
            </dd>
            <dt className="text-slate">Payer</dt>
            <dd>{r.patient.payer}</dd>
            <dt className="text-slate">Attending</dt>
            <dd>{r.patient.attending}</dd>
          </dl>
          <dl className="grid grid-cols-[9rem_minmax(0,1fr)] gap-y-1.5">
            <dt className="text-slate">Authorized</dt>
            <dd>
              {when(r.authorizedAt)}
              <span className="block text-xs text-slate">by {r.authorizedBy}</span>
            </dd>
            <dt className="text-slate">All departments clear</dt>
            <dd>{when(r.clearedAt)}</dd>
            <dt className="text-slate">Discharged</dt>
            <dd>{when(r.dischargedAt)}</dd>
            <dt className="text-slate">Clearance time</dt>
            <dd className="tnum font-semibold">{r.clearanceMinutes !== null ? duration(r.clearanceMinutes) : `${mins(r.elapsedMinutes)} so far`}</dd>
          </dl>
        </section>

        {r.summary && <p className="border-b border-ink/10 py-4 text-sm leading-relaxed">{r.summary}</p>}

        <section className="py-5">
          <h2 className="text-base font-bold">Departments</h2>
          {slowest && slowest.minutesToClear! > 0 && (
            <p className="mt-1 text-sm text-slate">
              {DEPT_LABEL[slowest.department]} was the last to clear, {duration(slowest.minutesToClear!)} after authorization.
            </p>
          )}
          <table className="mt-3 w-full text-left text-sm">
            <thead className="text-xs text-slate">
              <tr className="border-b border-ink/10">
                <th className="py-2 pr-3 font-semibold">Department</th>
                <th className="py-2 pr-3 font-semibold">Status</th>
                <th className="py-2 pr-3 font-semibold">Cleared</th>
                <th className="py-2 pr-3 text-right font-semibold">After authorization</th>
                <th className="py-2 text-right font-semibold">Blockers</th>
              </tr>
            </thead>
            <tbody className="tnum">
              {r.departments.map((d) => (
                <tr key={d.department} className="border-b border-ink/5">
                  <td className="py-2 pr-3 font-semibold">{DEPT_LABEL[d.department]}</td>
                  <td className="py-2 pr-3">{TASK_LABEL[d.status]}</td>
                  <td className="py-2 pr-3">{when(d.clearedAt)}</td>
                  <td className="py-2 pr-3 text-right">{mins(d.minutesToClear)}</td>
                  <td className="py-2 text-right">{d.blockers}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="py-5">
          <h2 className="text-base font-bold">What held it up</h2>
          {r.blockers.length === 0 ? (
            <p className="mt-2 text-sm text-slate">No blockers were found.</p>
          ) : (
            <table className="mt-3 w-full text-left text-sm">
              <thead className="text-xs text-slate">
                <tr className="border-b border-ink/10">
                  <th className="py-2 pr-3 font-semibold">Department</th>
                  <th className="py-2 pr-3 font-semibold">Blocker</th>
                  <th className="py-2 pr-3 text-right font-semibold">Open for</th>
                  <th className="py-2 text-right font-semibold">Status</th>
                </tr>
              </thead>
              <tbody>
                {r.blockers.map((b) => (
                  <tr key={b.id} className="border-b border-ink/5 align-top">
                    <td className="py-2 pr-3 font-semibold">{DEPT_LABEL[b.department]}</td>
                    <td className="py-2 pr-3">
                      <span className="text-slate">{KIND_LABEL[b.kind] ?? b.kind}.</span> {b.summary}
                    </td>
                    <td className="tnum py-2 pr-3 text-right">{mins(b.minutesOpen)}</td>
                    <td className="py-2 text-right">{b.status === "resolved" ? "Resolved" : "Open"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>

        <section className="py-5">
          <h2 className="text-base font-bold">Actions and decisions</h2>
          {r.actions.length === 0 ? (
            <p className="mt-2 text-sm text-slate">CareFlow took no actions on this discharge.</p>
          ) : (
            <ul className="mt-3 flex flex-col gap-3 text-sm">
              {r.actions.map((a) => (
                <li key={a.id} className="border-b border-ink/5 pb-3">
                  <p>
                    <span className="font-semibold">{a.description}</span>
                  </p>
                  <p className="mt-0.5 text-xs text-slate">
                    {RISK_LABEL[a.risk]}. {ACTION_SHORT[a.status]}.{" "}
                    {a.requiresApproval
                      ? a.decidedBy
                        ? `Decided by ${a.decidedBy}, ${when(a.decidedAt)}${a.comment ? `: "${a.comment}"` : ""}.`
                        : `Waiting for the ${a.approverRole.toLowerCase()}.`
                      : "Ran without a reviewer under the low-risk policy."}
                  </p>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="py-5">
          <h2 className="text-base font-bold">Decisions and agent steps</h2>
          <ol className="mt-3 flex flex-col gap-1.5 text-sm">
            {r.timeline.map((e, i) => (
              <li key={i} className="grid grid-cols-[9.5rem_minmax(0,1fr)] gap-3">
                <span className="tnum text-xs text-slate">{when(e.at)}</span>
                <span>
                  <span className="font-semibold">{e.event}</span> <span className="text-slate">({e.actor})</span>
                  {e.detail && <span className="block text-xs text-slate">{e.detail}</span>}
                </span>
              </li>
            ))}
          </ol>
        </section>

        <footer className="border-t border-ink/10 pt-4 text-xs text-slate">
          CareFlow coordinates administrative discharge work. Clinical decisions are made and recorded by the treating team.
        </footer>
      </article>
    </div>
  );
}
