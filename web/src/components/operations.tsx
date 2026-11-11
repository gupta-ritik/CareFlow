"use client";

import { Download } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Button, ErrorText, PageHeader, Panel, Skeleton, inputClass } from "./ui";
import { downloadCsv } from "@/lib/api";
import { DEPT_LABEL, KIND_LABEL, duration } from "@/lib/format";
import { useAgents, useDashboard } from "@/lib/queries";
import type { Department } from "@/lib/types";

const INK = "#0f2236";
const SLATE = "#56677a";
const GRID = "rgba(15,34,54,0.08)";

function pct(v: number | null | undefined) {
  return v === null || v === undefined ? "n/a" : `${Math.round(v * 100)}%`;
}

function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-sm text-slate">{label}</dt>
      <dd className="tnum mt-0.5 text-2xl font-bold tracking-[-0.02em] text-ink">{value}</dd>
      {note && <p className="mt-0.5 text-xs text-faint">{note}</p>}
    </div>
  );
}

function HBar({
  data,
  valueKey,
  nameKey,
  color,
  unit,
  height,
}: {
  data: Record<string, unknown>[];
  valueKey: string;
  nameKey: string;
  color: string;
  unit?: string;
  height?: number;
}) {
  return (
    <div style={{ height: height ?? Math.max(120, data.length * 36 + 24) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 40, bottom: 4, left: 4 }} barCategoryGap={8}>
          <CartesianGrid horizontal={false} stroke={GRID} />
          <XAxis type="number" allowDecimals={false} tick={{ fill: SLATE, fontSize: 12 }} axisLine={false} tickLine={false} />
          <YAxis type="category" dataKey={nameKey} width={170} tick={{ fill: INK, fontSize: 13 }} axisLine={false} tickLine={false} />
          <Tooltip
            cursor={{ fill: "rgba(42,99,201,0.06)" }}
            contentStyle={{ borderRadius: 12, border: "1px solid rgba(255,255,255,.8)", background: "rgba(255,255,255,.95)", color: INK, fontSize: 13 }}
            formatter={(v) => [`${v}${unit ?? ""}`, ""]}
            separator=""
          />
          <Bar dataKey={valueKey} fill={color} barSize={14} radius={[0, 4, 4, 0]} isAnimationActive={false}>
            <LabelList dataKey={valueKey} position="right" fill={SLATE} fontSize={12} formatter={(v) => `${v}${unit ?? ""}`} />
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function Export() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [state, setState] = useState<{ busy?: boolean; error?: unknown }>({});
  return (
    <Panel className="p-4 sm:p-5">
      <h2 className="text-lg font-bold text-ink">Export discharges</h2>
      <p className="mt-1 text-sm text-slate">
        One row per discharge: authorization, clearance and discharge times, blockers, actions and the slowest department. Opens in Excel or Google Sheets.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="text-sm font-semibold text-ink">
          From
          <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={`${inputClass} mt-1.5 w-44`} />
        </label>
        <label className="text-sm font-semibold text-ink">
          To
          <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={`${inputClass} mt-1.5 w-44`} />
        </label>
        <Button
          disabled={state.busy}
          onClick={async () => {
            setState({ busy: true });
            try {
              await downloadCsv(from, to);
              setState({});
            } catch (error) {
              setState({ error });
            }
          }}
        >
          <Download className="size-4" aria-hidden /> {state.busy ? "Preparing" : "Download CSV"}
        </Button>
      </div>
      <div className="mt-3">
        <ErrorText error={state.error} />
      </div>
    </Panel>
  );
}

export function Operations() {
  const { data: d, isPending, error } = useDashboard();
  const { data: agents } = useAgents();

  if (error) {
    return (
      <>
        <PageHeader title="Operations" />
        <Panel className="p-6 text-sm text-slate">{error.message}</Panel>
      </>
    );
  }

  const depts = (d?.departments ?? []).map((x) => ({ ...x, name: DEPT_LABEL[x.department as Department] }));
  const kinds = (d?.blockerKinds ?? []).map((k) => ({ name: KIND_LABEL[k.kind] ?? k.kind, count: k.count }));
  const agentRows = (agents ?? [])
    .filter((a) => a.calls > 0)
    .map((a) => ({ name: a.agent, ms: Math.round(a.avgLatencyMs) }))
    .sort((a, b) => b.ms - a.ms);

  return (
    <>
      <PageHeader title="Operations">
        <p>How administrative clearance is moving across the hospital, and where it gets stuck. Updates every 10 seconds.</p>
      </PageHeader>

      {isPending || !d ? (
        <div className="flex flex-col gap-4">
          <Skeleton className="h-28" />
          <Skeleton className="h-72" />
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          <Panel strong className="p-5 sm:p-6">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-5 md:grid-cols-4">
              <Figure label="Waiting on administration" value={String(d.activeWorkflows)} note={`${d.blocked} blocked, ${d.awaitingApproval} need approval`} />
              <Figure label="Median wait so far" value={duration(d.medianWaitMinutes)} note="since clinical authorization" />
              <Figure
                label="Average clearance time"
                value={d.avgClearanceMinutes === null ? "n/a" : duration(d.avgClearanceMinutes)}
                note={`${d.readyToDischarge} ready, ${d.discharged} discharged`}
              />
              <Figure label="Needed a person" value={pct(d.humanInterventionRate)} note="of workflows had an action needing approval" />
              <Figure label="Blockers resolved" value={pct(d.recoveryRate)} note="of all blockers found so far" />
              <Figure label="Actions run" value={String(d.actions.total)} note={`${d.actions.automatic} automatic, ${d.actions.needingApproval} reviewed`} />
              <Figure label="Verified in source system" value={String(d.actions.verified)} note={`${d.actions.verificationFailed} failed verification`} />
              <Figure
                label="Average decision time"
                value={d.avgDecisionMinutes === null ? "n/a" : duration(Math.round(d.avgDecisionMinutes))}
                note="from proposal to reviewer decision"
              />
            </dl>
          </Panel>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <Panel className="min-w-0 p-4 sm:p-5">
              <h2 className="text-lg font-bold text-ink">Open blockers by department</h2>
              <p className="mt-1 text-sm text-slate">Where discharges are waiting right now.</p>
              <div className="mt-3">
                <HBar data={depts} valueKey="openBlockers" nameKey="name" color="#c9821b" />
              </div>
              <div className="sr-only">
                <table>
                  <caption>Open blockers and pending tasks by department</caption>
                  <thead>
                    <tr>
                      <th>Department</th>
                      <th>Open blockers</th>
                      <th>Pending tasks</th>
                      <th>Average minutes blocked</th>
                    </tr>
                  </thead>
                  <tbody>
                    {depts.map((x) => (
                      <tr key={x.department}>
                        <td>{x.name}</td>
                        <td>{x.openBlockers}</td>
                        <td>{x.pendingTasks}</td>
                        <td>{x.avgBlockedMinutes}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            <Panel className="min-w-0 p-4 sm:p-5">
              <h2 className="text-lg font-bold text-ink">What&apos;s blocking</h2>
              <p className="mt-1 text-sm text-slate">Open blockers by type.</p>
              <div className="mt-3">
                {kinds.length ? <HBar data={kinds} valueKey="count" nameKey="name" color="#c9821b" /> : <p className="text-sm text-slate">No open blockers.</p>}
              </div>
            </Panel>

            <Panel className="min-w-0 p-4 sm:p-5">
              <h2 className="text-lg font-bold text-ink">Department queues</h2>
              <p className="mt-1 text-sm text-slate">Tasks not yet cleared, with how long their blockers have been open.</p>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full min-w-[420px] text-left text-sm">
                  <thead className="text-xs text-faint">
                    <tr>
                      <th className="pb-2 pr-3 font-semibold">Department</th>
                      <th className="pb-2 pr-3 text-right font-semibold">Pending tasks</th>
                      <th className="pb-2 pr-3 text-right font-semibold">Open blockers</th>
                      <th className="pb-2 text-right font-semibold">Avg time blocked</th>
                    </tr>
                  </thead>
                  <tbody className="tnum divide-y divide-line">
                    {depts.map((x) => (
                      <tr key={x.department}>
                        <td className="py-2.5 pr-3 font-semibold text-ink">{x.name}</td>
                        <td className="py-2.5 pr-3 text-right text-slate">{x.pendingTasks}</td>
                        <td className="py-2.5 pr-3 text-right text-slate">{x.openBlockers}</td>
                        <td className="py-2.5 text-right text-slate">{x.openBlockers ? duration(x.avgBlockedMinutes) : "none"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Panel>

            <Panel className="min-w-0 p-4 sm:p-5">
              <h2 className="text-lg font-bold text-ink">Agent latency</h2>
              <p className="mt-1 text-sm text-slate">
                Average time per run. Department checks include retries against slow systems.
                {(agents ?? []).some((a) => a.fallbackCalls > 0 && a.llmCalls === 0) &&
                  " LLM agents near 0 ms are running on their rule-based fallback (no Groq key set)."}
              </p>
              <div className="mt-3">
                {agentRows.length ? (
                  <HBar data={agentRows} valueKey="ms" nameKey="name" color="#2a63c9" unit=" ms" />
                ) : (
                  <p className="text-sm text-slate">Agent stats need the Auditor or coordinator role.</p>
                )}
              </div>
            </Panel>
          </div>
          <Export />
        </div>
      )}
    </>
  );
}
