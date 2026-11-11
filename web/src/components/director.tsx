"use client";

import clsx from "clsx";
import Link from "next/link";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AskPanel } from "./ask";
import { PageHeader, Panel, Skeleton, WorkflowPill } from "./ui";
import { DEPT_LABEL, duration } from "@/lib/format";
import { useExecutive } from "@/lib/queries";
import { useCan } from "@/lib/session";
import type { ExecutiveOverview, IntegrationSystem } from "@/lib/types";

const INK = "#0f2236";
const SLATE = "#56677a";
const GRID = "rgba(15,34,54,0.08)";
const HARBOR = "#2a63c9";
const TIDE = "#0e8f86";

const SYSTEM_LABEL: Record<IntegrationSystem, string> = { his: "Hospital system (ADT)", tpa: "TPA / payer", pharmacy: "Pharmacy", lis: "Laboratory" };
const PAYER_LABEL: Record<string, string> = { self_pay: "Self-pay", cashless: "Cashless insurance", government: "Government scheme" };

function mins(v: number | null | undefined) {
  return v === null || v === undefined ? "n/a" : duration(Math.round(v));
}

function Stat({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: "warn" | "good" }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-slate">{label}</dt>
      <dd
        className={clsx(
          "tnum mt-0.5 text-2xl font-bold tracking-[-0.02em]",
          tone === "warn" ? "text-amber-ink" : tone === "good" ? "text-success" : "text-ink",
        )}
      >
        {value}
      </dd>
      {note && <p className="mt-0.5 text-xs text-faint">{note}</p>}
    </div>
  );
}

function Trend({ data }: { data: ExecutiveOverview["trend"] }) {
  const rows = data.map((d) => ({ ...d, day: new Date(d.date).toLocaleDateString("en-IN", { day: "numeric", month: "short" }) }));
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -16 }} barGap={2}>
          <CartesianGrid vertical={false} stroke={GRID} />
          <XAxis dataKey="day" tick={{ fill: SLATE, fontSize: 11 }} axisLine={false} tickLine={false} interval="preserveStartEnd" />
          <YAxis allowDecimals={false} tick={{ fill: SLATE, fontSize: 12 }} axisLine={false} tickLine={false} />
          <Tooltip
            cursor={{ fill: "rgba(42,99,201,0.06)" }}
            contentStyle={{ borderRadius: 12, border: "1px solid rgba(255,255,255,.8)", background: "rgba(255,255,255,.95)", color: INK, fontSize: 13 }}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: SLATE }} iconType="circle" iconSize={8} />
          <Bar dataKey="authorized" name="Authorized" fill={HARBOR} radius={[3, 3, 0, 0]} isAnimationActive={false} />
          <Bar dataKey="discharged" name="Discharged" fill={TIDE} radius={[3, 3, 0, 0]} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
      <div className="sr-only">
        <table>
          <caption>Discharges per day</caption>
          <thead>
            <tr>
              <th>Date</th>
              <th>Authorized</th>
              <th>Discharged</th>
              <th>Median clearance minutes</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.date}>
                <td>{d.date}</td>
                <td>{d.authorized}</td>
                <td>{d.discharged}</td>
                <td>{d.medianClearanceMinutes ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Meter({ value, total, color }: { value: number; total: number; color: string }) {
  const pct = total ? Math.round((value / total) * 100) : 0;
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-ink/[0.07]" aria-hidden>
      <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

export function Director() {
  const [days, setDays] = useState(30);
  const allowed = useCan("executive:read");
  const { data: d, isPending, error } = useExecutive(days);

  if (!allowed)
    return (
      <>
        <PageHeader title="Director dashboard" />
        <Panel className="p-6 text-sm text-slate">This dashboard is for the hospital director and administrators. Your role doesn&apos;t include it.</Panel>
      </>
    );

  return (
    <>
      <PageHeader title="Director dashboard">
        <p>
          The whole hospital&apos;s discharge flow at a glance: who is waiting, where it&apos;s stuck, what needs a decision, and how beds and connected systems
          are doing. Updates every 15 seconds.
        </p>
      </PageHeader>

      <AskPanel />

      <div role="group" aria-label="Time window" className="mb-4 flex w-fit gap-1 rounded-[10px] bg-surface-2 p-1 ring-1 ring-inset ring-line">
        {[7, 30, 90].map((n) => (
          <button
            key={n}
            type="button"
            aria-pressed={days === n}
            onClick={() => setDays(n)}
            className={clsx(
              "rounded-[7px] px-3 py-1.5 text-[13px] font-semibold",
              days === n ? "bg-surface text-ink shadow-[var(--shadow-card)] ring-1 ring-line" : "text-slate hover:text-ink",
            )}
          >
            Last {n} days
          </button>
        ))}
      </div>

      {error ? (
        <Panel className="p-6 text-sm text-coral">{error.message}</Panel>
      ) : isPending || !d ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-32" />
          <Skeleton className="h-72" />
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          {/* ---- census ---- */}
          <Panel strong className="p-5 sm:p-6">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-5 md:grid-cols-4">
              <Stat
                label="Patients in hospital"
                value={String(d.census.inHospital)}
                note={`${d.census.awaitingAuthorization} not yet authorized for discharge`}
              />
              <Stat
                label="In discharge clearance"
                value={String(d.census.inClearance)}
                note={`${d.census.blocked} blocked, ${d.census.awaitingApproval} need approval`}
                tone={d.census.blocked ? "warn" : undefined}
              />
              <Stat
                label="Ready to leave"
                value={String(d.census.readyToDischarge)}
                note="cleared by every department"
                tone={d.census.readyToDischarge ? "good" : undefined}
              />
              <Stat
                label="Discharged today"
                value={String(d.census.dischargedToday)}
                note={`${d.census.dischargedInWindow} in the last ${d.windowDays} days`}
              />
              <Stat label="Median clearance time" value={mins(d.flow.clearanceMinutes.median)} note={`90% within ${mins(d.flow.clearanceMinutes.p90)}`} />
              <Stat label="Authorization to exit" value={mins(d.flow.authorizationToExitMinutes.median)} note="median, doctor's decision to patient leaving" />
              <Stat
                label="Ready but still in bed"
                value={mins(d.flow.readyToExitMinutes.median)}
                note="median wait after every department cleared"
                tone={(d.flow.readyToExitMinutes.median ?? 0) > 60 ? "warn" : undefined}
              />
              <Stat
                label="Bed occupancy"
                value={d.beds.occupancy === null ? "n/a" : `${Math.round(d.beds.occupancy * 100)}%`}
                note={`${d.beds.occupied} of ${d.beds.total} beds, ${d.beds.cleaning} being cleaned`}
              />
            </dl>
          </Panel>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
            {/* ---- longest waits ---- */}
            <Panel className="min-w-0 p-4 sm:p-5">
              <h2 className="text-lg font-bold text-ink">Longest waits right now</h2>
              <p className="mt-1 text-sm text-slate">Patients authorized for discharge who haven&apos;t left yet, longest first.</p>
              {d.longestWaits.length === 0 ? (
                <p className="mt-4 text-sm text-slate">Nobody is waiting on discharge clearance.</p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[520px] text-left text-sm">
                    <thead className="text-xs text-faint">
                      <tr>
                        <th className="pb-2 pr-3 font-semibold">Patient</th>
                        <th className="pb-2 pr-3 font-semibold">Status</th>
                        <th className="pb-2 pr-3 font-semibold">Waiting on</th>
                        <th className="pb-2 text-right font-semibold">Waiting</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line">
                      {d.longestWaits.map((w) => (
                        <tr key={w.workflowId}>
                          <td className="py-2.5 pr-3">
                            <Link href={`/workflows/${w.workflowId}`} className="font-semibold text-ink hover:text-tide-deep hover:underline">
                              {w.patient}
                            </Link>
                            <p className="tnum text-xs text-faint">
                              {w.ward}
                              {w.bed && `, ${w.bed}`} / {w.mrn}
                            </p>
                          </td>
                          <td className="py-2.5 pr-3">
                            <WorkflowPill status={w.status} />
                          </td>
                          <td className="py-2.5 pr-3 text-slate">
                            {w.blockingDepartments.length ? w.blockingDepartments.map((x) => DEPT_LABEL[x]).join(", ") : "nothing"}
                          </td>
                          <td
                            className={clsx(
                              "tnum py-2.5 text-right font-semibold",
                              w.waitMinutes > 180 ? "text-coral" : w.waitMinutes > 90 ? "text-amber-ink" : "text-ink",
                            )}
                          >
                            {duration(w.waitMinutes)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>

            {/* ---- decisions ---- */}
            <Panel className="min-w-0 p-4 sm:p-5">
              <h2 className="text-lg font-bold text-ink">Decisions waiting</h2>
              <p className="mt-1 text-sm text-slate">Actions CareFlow proposed that need a person. As director you can decide any of them.</p>
              <dl className="mt-4 grid grid-cols-2 gap-4">
                <Stat
                  label="Pending approvals"
                  value={String(d.approvals.pending)}
                  tone={d.approvals.pending ? "warn" : undefined}
                  note={d.approvals.oldestPendingMinutes !== null ? `oldest ${duration(d.approvals.oldestPendingMinutes)}` : "none waiting"}
                />
                <Stat
                  label="Average decision time"
                  value={d.approvals.avgDecisionMinutes === null ? "n/a" : duration(Math.round(d.approvals.avgDecisionMinutes))}
                  note={`${d.approvals.decided} decided, ${d.approvals.rejected} rejected`}
                />
              </dl>
              {Object.keys(d.approvals.byRole).length > 0 && (
                <ul className="mt-4 flex flex-col gap-1.5 text-sm">
                  {Object.entries(d.approvals.byRole).map(([role, n]) => (
                    <li key={role} className="flex justify-between gap-3">
                      <span className="text-slate">{role}</span>
                      <span className="tnum font-semibold text-ink">{n}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-4 text-sm text-slate">
                {d.approvals.automaticActions} low-risk reminders ran automatically in this period.{" "}
                <Link href="/approvals" className="font-semibold text-tide-deep hover:underline">
                  Open approvals
                </Link>
              </p>
            </Panel>
          </div>

          {/* ---- departments ---- */}
          <Panel className="min-w-0 p-4 sm:p-5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-bold text-ink">Departments</h2>
              {d.bottleneck && (
                <p className="text-sm text-amber-ink">
                  Current bottleneck: <span className="font-semibold">{DEPT_LABEL[d.bottleneck]}</span>
                </p>
              )}
            </div>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[620px] text-left text-sm">
                <thead className="text-xs text-faint">
                  <tr>
                    <th className="pb-2 pr-3 font-semibold">Department</th>
                    <th className="pb-2 pr-3 text-right font-semibold">Open blockers</th>
                    <th className="pb-2 pr-3 text-right font-semibold">Oldest blocker</th>
                    <th className="pb-2 pr-3 text-right font-semibold">Patients pending</th>
                    <th className="pb-2 pr-3 text-right font-semibold">Avg time to clear</th>
                    <th className="pb-2 text-right font-semibold">Missed time limits</th>
                  </tr>
                </thead>
                <tbody className="tnum divide-y divide-line">
                  {d.departments.map((x) => (
                    <tr key={x.department} className={x.department === d.bottleneck ? "bg-amber-soft/40" : undefined}>
                      <td className="py-2.5 pr-3 font-semibold text-ink">
                        <Link href={`/queue?dept=${x.department}`} className="hover:text-tide-deep hover:underline">
                          {DEPT_LABEL[x.department]}
                        </Link>
                      </td>
                      <td className={clsx("py-2.5 pr-3 text-right", x.openBlockers ? "font-semibold text-amber-ink" : "text-slate")}>{x.openBlockers}</td>
                      <td className="py-2.5 pr-3 text-right text-slate">{x.oldestBlockerMinutes === null ? "none" : duration(x.oldestBlockerMinutes)}</td>
                      <td className="py-2.5 pr-3 text-right text-slate">{x.pendingTasks}</td>
                      <td className="py-2.5 pr-3 text-right text-slate">{mins(x.avgMinutesToClear)}</td>
                      <td className={clsx("py-2.5 text-right", x.slaBreaches ? "font-semibold text-coral" : "text-slate")}>{x.slaBreaches}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-3 text-xs text-faint">
              {d.flow.blockersFound} blockers found and {d.flow.blockersResolved} resolved in the last {d.windowDays} days; {d.flow.slaBreaches} were missed
              time limits (pharmacy, payer or billing delays).
            </p>
          </Panel>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <Panel className="min-w-0 p-4 sm:p-5">
              <h2 className="text-lg font-bold text-ink">Last 14 days</h2>
              <p className="mt-1 text-sm text-slate">Discharges authorized by clinicians and patients who actually left, per day.</p>
              <div className="mt-3">
                <Trend data={d.trend} />
              </div>
            </Panel>

            {/* ---- beds ---- */}
            <Panel className="min-w-0 p-4 sm:p-5">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="text-lg font-bold text-ink">Beds by ward</h2>
                <Link href="/beds" className="text-sm font-semibold text-tide-deep hover:underline">
                  Bed board
                </Link>
              </div>
              {d.beds.total === 0 ? (
                <p className="mt-3 text-sm text-slate">No beds recorded yet. Beds appear as patients are admitted, or add them on the Beds page.</p>
              ) : (
                <ul className="mt-3 flex flex-col gap-3">
                  {d.beds.byWard.map((w) => (
                    <li key={w.ward}>
                      <div className="flex justify-between text-sm">
                        <span className="font-semibold text-ink">{w.ward}</span>
                        <span className="tnum text-slate">
                          {w.occupied ?? 0}/{w.total} occupied{w.cleaning ? `, ${w.cleaning} cleaning` : ""}
                        </span>
                      </div>
                      <div className="mt-1.5">
                        <Meter value={w.occupied ?? 0} total={w.total} color={HARBOR} />
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>

            {/* ---- integrations ---- */}
            <Panel className="min-w-0 p-4 sm:p-5">
              <div className="flex items-baseline justify-between gap-2">
                <h2 className="text-lg font-bold text-ink">Connected systems</h2>
                <Link href="/integrations" className="text-sm font-semibold text-tide-deep hover:underline">
                  Details
                </Link>
              </div>
              <p className="mt-1 text-sm text-slate">Messages exchanged in the last 24 hours.</p>
              <ul className="mt-3 divide-y divide-line text-sm">
                {(Object.keys(SYSTEM_LABEL) as IntegrationSystem[]).map((sys) => {
                  const x = d.integrations[sys];
                  return (
                    <li key={sys} className="flex items-center justify-between gap-3 py-2">
                      <span className="flex items-center gap-2">
                        <span className={clsx("size-2 rounded-full", x.errors24h ? "bg-coral" : x.messages24h ? "bg-tide" : "bg-ink/20")} aria-hidden />
                        <span className="text-ink">{SYSTEM_LABEL[sys]}</span>
                      </span>
                      <span className="tnum text-slate">
                        {x.messages24h} messages{x.errors24h ? <span className="font-semibold text-coral">, {x.errors24h} errors</span> : null}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </Panel>

            {/* ---- payer mix, AI, staff ---- */}
            <Panel className="min-w-0 p-4 sm:p-5">
              <h2 className="text-lg font-bold text-ink">Patients, agents and staff</h2>
              <dl className="mt-3 grid grid-cols-3 gap-3">
                {Object.entries(PAYER_LABEL).map(([k, label]) => (
                  <Stat key={k} label={label} value={String(d.payerMix[k as keyof typeof d.payerMix] ?? 0)} />
                ))}
              </dl>
              <p className="mt-4 text-sm text-slate">
                CareFlow&apos;s agents ran {d.ai.runs} times in this period: {d.ai.llm} on Groq, {d.ai.fallback} on the rule-based fallback
                {d.ai.errors ? `, ${d.ai.errors} errors` : ""}. {d.ai.tokens.toLocaleString("en-IN")} tokens used.
              </p>
              {d.staffActivity.length > 0 && (
                <>
                  <h3 className="mt-4 text-sm font-semibold text-ink">Most active staff</h3>
                  <ul className="mt-2 flex flex-col gap-1 text-sm">
                    {d.staffActivity.map((s) => (
                      <li key={s.actor} className="flex justify-between gap-3">
                        <span className="truncate text-slate">{s.actor}</span>
                        <span className="tnum font-semibold text-ink">{s.actions}</span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </Panel>
          </div>
          <p className="text-xs text-faint">Generated {new Date(d.generatedAt).toLocaleString("en-IN")}. Days are counted in UTC.</p>
        </div>
      )}
    </>
  );
}
