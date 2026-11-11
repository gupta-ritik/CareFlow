"use client";

import clsx from "clsx";
import { Bot, ChevronLeft, ChevronRight, History, Lock, Search, Settings2, UserRound } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Badge, Button, Drawer, EmptyState, EvidenceRef, PageHeader, Panel, Section, Skeleton, Tabs, inputClass, selectClass, type Tone } from "./ui";
import { clock, since } from "@/lib/format";
import { useAgents, useAudit } from "@/lib/queries";
import type { ActorKind, AuditEvent } from "@/lib/types";

const KIND: Record<ActorKind, { label: string; tone: Tone; icon: typeof Bot }> = {
  agent: { label: "Agent", tone: "brand", icon: Bot },
  human: { label: "Person", tone: "violet", icon: UserRound },
  system: { label: "System", tone: "neutral", icon: Settings2 },
};
const PAGE = 50;
type Range = "all" | "1h" | "24h" | "7d";
const RANGE_MS: Record<Exclude<Range, "all">, number> = { "1h": 3_600_000, "24h": 86_400_000, "7d": 7 * 86_400_000 };

function when(iso: string) {
  const d = new Date(iso);
  return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function EventDetail({ e, onClose }: { e: AuditEvent; onClose: () => void }) {
  const k = KIND[e.actorKind];
  return (
    <Drawer open onClose={onClose} title={e.event} subtitle={<span className="tnum font-mono text-xs">{e.id}</span>}>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 text-sm">
        <div>
          <dt className="text-xs text-faint">When</dt>
          <dd className="mt-0.5 text-ink">
            {when(e.at)} <span className="text-faint">({since(e.at)})</span>
          </dd>
        </div>
        <div>
          <dt className="text-xs text-faint">Actor</dt>
          <dd className="mt-0.5 flex flex-wrap items-center gap-1.5 text-ink">
            <Badge tone={k.tone} dot={false}>
              {k.label}
            </Badge>
            {e.actor}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-faint">Discharge workflow</dt>
          <dd className="mt-0.5">
            {e.workflowId && e.workflowId !== "-" ? (
              <Link href={`/workflows/${e.workflowId}`} className="font-semibold text-tide-deep hover:underline">
                {e.workflowId}
              </Link>
            ) : (
              <span className="text-slate">None</span>
            )}
          </dd>
        </div>
        {(e.latencyMs != null || e.tokens != null) && (
          <div>
            <dt className="text-xs text-faint">Execution</dt>
            <dd className="tnum mt-0.5 text-ink">
              {e.latencyMs != null && `${e.latencyMs >= 1000 ? `${(e.latencyMs / 1000).toFixed(1)} s` : `${e.latencyMs} ms`}`}
              {e.tokens != null && ` · ${e.tokens.toLocaleString("en-IN")} tokens`}
            </dd>
          </div>
        )}
        <div className="col-span-2">
          <dt className="text-xs text-faint">Details</dt>
          <dd className="mt-1 whitespace-pre-wrap rounded-xl bg-surface-2 px-3.5 py-3 text-[13px] leading-relaxed text-ink ring-1 ring-line">{e.detail || "No further detail recorded."}</dd>
        </div>
        {e.evidenceIds && e.evidenceIds.length > 0 && (
          <div className="col-span-2">
            <dt className="text-xs text-faint">Evidence referenced</dt>
            <dd className="mt-1.5 flex flex-wrap gap-1.5">
              {e.evidenceIds.map((id) => (
                <EvidenceRef key={id} id={id} />
              ))}
            </dd>
          </div>
        )}
      </dl>
      <p className="mt-6 flex items-start gap-2 text-xs leading-relaxed text-faint">
        <Lock className="mt-0.5 size-3.5 shrink-0" aria-hidden /> Audit records are append-only. They can be read and exported, never edited or deleted.
      </p>
    </Drawer>
  );
}

export function Audit() {
  const { data, isPending, error } = useAudit();
  const { data: agents, error: agentsError } = useAgents();
  const [kind, setKind] = useState<"all" | ActorKind>("all");
  const [range, setRange] = useState<Range>("all");
  const [actor, setActor] = useState("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);

  const actors = useMemo(() => [...new Set((data ?? []).map((e) => e.actor))].sort(), [data]);
  const events = useMemo(() => {
    const term = q.trim().toLowerCase();
    // Read once per filter change: a live "now" in render would make the list jump while you read it.
    // eslint-disable-next-line react-hooks/purity
    const cutoff = range === "all" ? 0 : Date.now() - RANGE_MS[range];
    return (data ?? []).filter(
      (e) =>
        (kind === "all" || e.actorKind === kind) &&
        (!actor || e.actor === actor) &&
        (!cutoff || new Date(e.at).getTime() >= cutoff) &&
        (!term || [e.id, e.workflowId, e.event, e.detail, e.actor].some((s) => s?.toLowerCase().includes(term))),
    );
  }, [data, kind, actor, range, q]);
  const pages = Math.max(1, Math.ceil(events.length / PAGE));
  const current = Math.min(page, pages - 1);
  const shown = events.slice(current * PAGE, current * PAGE + PAGE);
  const open = data?.find((e) => e.id === openId);
  const reset = <T,>(fn: (v: T) => void) => (v: T) => {
    fn(v);
    setPage(0);
  };

  return (
    <>
      <PageHeader title="Audit trail">
        Every agent step, human decision and system action, with the evidence it relied on. Agent reasoning is summarised, never shown in full.
      </PageHeader>

      <Panel className="mb-5">
        <div className="flex flex-col gap-3 border-b border-line p-3 sm:p-4 xl:flex-row xl:items-center xl:justify-between">
          <Tabs<"all" | ActorKind>
            wrap
            label="Actor type"
            value={kind}
            onChange={reset(setKind)}
            options={[
              { value: "all", label: "Everything", count: data?.length },
              { value: "agent", label: "Agents", count: data?.filter((e) => e.actorKind === "agent").length },
              { value: "human", label: "People", count: data?.filter((e) => e.actorKind === "human").length },
              { value: "system", label: "System", count: data?.filter((e) => e.actorKind === "system").length },
            ]}
          />
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="relative sm:w-60">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
              <input type="search" value={q} onChange={(e) => reset(setQ)(e.target.value)} placeholder="Event, workflow, detail…" aria-label="Search events" className={clsx(inputClass, "pl-9")} />
            </div>
            <select value={actor} onChange={(e) => reset(setActor)(e.target.value)} aria-label="Actor" className={clsx(selectClass, "sm:w-48")}>
              <option value="">All actors</option>
              {actors.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </select>
            <select value={range} onChange={(e) => reset(setRange)(e.target.value as Range)} aria-label="Time range" className={clsx(selectClass, "sm:w-40")}>
              <option value="all">Any time</option>
              <option value="1h">Last hour</option>
              <option value="24h">Last 24 hours</option>
              <option value="7d">Last 7 days</option>
            </select>
          </div>
        </div>

        {error ? (
          <p className="p-5 text-sm text-coral-ink">{error.message}</p>
        ) : isPending ? (
          <div className="flex flex-col gap-2 p-4">
            {[0, 1, 2, 3, 4].map((i) => (
              <Skeleton key={i} className="h-10" />
            ))}
          </div>
        ) : shown.length === 0 ? (
          <EmptyState icon={History} title="No events match">
            Adjust the filters above to see more of the audit trail.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] text-left text-sm">
              <thead className="bg-surface-2 text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">
                <tr>
                  <th scope="col" className="px-4 py-2.5 font-semibold sm:px-5">
                    Time
                  </th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">
                    Actor
                  </th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">
                    Action
                  </th>
                  <th scope="col" className="px-3 py-2.5 font-semibold">
                    Workflow
                  </th>
                  <th scope="col" className="px-4 py-2.5 text-right font-semibold sm:px-5">
                    Event ID
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {shown.map((e) => {
                  const k = KIND[e.actorKind];
                  const Icon = k.icon;
                  return (
                    <tr key={e.id} className="cursor-pointer hover:bg-hover" onClick={() => setOpenId(e.id)}>
                      <td className="tnum whitespace-nowrap px-4 py-3 text-[13px] text-slate sm:px-5">
                        <span className="block text-ink">{clock(e.at)}</span>
                        <span className="text-xs text-faint">{new Date(e.at).toLocaleDateString("en-IN", { day: "2-digit", month: "short" })}</span>
                      </td>
                      <td className="px-3 py-3">
                        <span className="flex items-center gap-2">
                          <span className={clsx("grid size-6 shrink-0 place-items-center rounded-md", e.actorKind === "agent" ? "bg-tide-soft text-tide-deep" : e.actorKind === "human" ? "bg-violet-soft text-violet" : "bg-surface-2 text-slate ring-1 ring-line")}>
                            <Icon className="size-3.5" aria-hidden />
                          </span>
                          <span className="min-w-0">
                            <span className="block max-w-[14rem] truncate text-[13px] font-medium text-ink">{e.actor}</span>
                            <span className="text-xs text-faint">{k.label}</span>
                          </span>
                        </span>
                      </td>
                      <td className="max-w-[28rem] px-3 py-3">
                        <button type="button" onClick={() => setOpenId(e.id)} className="block text-left text-[13px] font-medium text-ink hover:underline">
                          {e.event}
                        </button>
                        {e.detail && <span className="line-clamp-1 text-xs text-slate">{e.detail}</span>}
                      </td>
                      <td className="tnum whitespace-nowrap px-3 py-3 font-mono text-xs text-slate">{e.workflowId && e.workflowId !== "-" ? e.workflowId : "—"}</td>
                      <td className="tnum whitespace-nowrap px-4 py-3 text-right font-mono text-xs text-faint sm:px-5">{e.id}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {events.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-4 py-3 text-xs text-slate sm:px-5">
            <span className="tnum">
              {current * PAGE + 1}–{Math.min(events.length, (current + 1) * PAGE)} of {events.length} events
            </span>
            <div className="flex items-center gap-1">
              <Button variant="secondary" size="sm" disabled={current === 0} onClick={() => setPage(current - 1)} aria-label="Previous page">
                <ChevronLeft className="size-4" aria-hidden /> Previous
              </Button>
              <span className="tnum px-2">
                Page {current + 1} of {pages}
              </span>
              <Button variant="secondary" size="sm" disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label="Next page">
                Next <ChevronRight className="size-4" aria-hidden />
              </Button>
            </div>
          </div>
        )}
      </Panel>

      <Section
        title="Agents and checks"
        description="Every node run is recorded. “Fallback” means the LLM was unavailable or its output was rejected, so the deterministic version ran."
        bodyClassName="p-0"
      >
        {agentsError ? (
          <p className="p-5 text-sm text-slate">{agentsError.message}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-left text-sm">
              <thead className="bg-surface-2 text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">
                <tr>
                  <th className="px-5 py-2.5 font-semibold">Agent</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Runs</th>
                  <th className="px-3 py-2.5 text-right font-semibold">LLM / fallback</th>
                  <th className="px-3 py-2.5 text-right font-semibold">Avg time</th>
                  <th className="px-5 py-2.5 text-right font-semibold">Tokens</th>
                </tr>
              </thead>
              <tbody className="tnum divide-y divide-line">
                {(agents ?? []).map((a) => (
                  <tr key={a.agent}>
                    <td className="px-5 py-3 font-medium text-ink">
                      {a.agent}
                      {a.errors > 0 && (
                        <Badge tone="danger" className="ml-2">
                          {a.errors} errors
                        </Badge>
                      )}
                    </td>
                    <td className="px-3 py-3 text-right text-slate">{a.calls}</td>
                    <td className="px-3 py-3 text-right text-slate">{a.llmCalls + a.fallbackCalls > 0 ? `${a.llmCalls} / ${a.fallbackCalls}` : "rules"}</td>
                    <td className="px-3 py-3 text-right text-slate">{a.avgLatencyMs >= 1000 ? `${(a.avgLatencyMs / 1000).toFixed(1)}s` : `${Math.round(a.avgLatencyMs)}ms`}</td>
                    <td className="px-5 py-3 text-right text-slate">{a.tokens.toLocaleString("en-IN")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="border-t border-line px-5 py-3 text-xs text-faint">LLM prompts are redacted before they leave the server. Set LANGSMITH_TRACING to see full traces.</p>
      </Section>

      {open && <EventDetail e={open} onClose={() => setOpenId(null)} />}
    </>
  );
}
