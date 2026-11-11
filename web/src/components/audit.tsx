"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import { Timeline } from "./timeline";
import { PageHeader, Panel, Skeleton } from "./ui";
import { useAgents, useAudit } from "@/lib/queries";
import type { ActorKind } from "@/lib/types";

const FILTERS: { key: "all" | ActorKind; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "agent", label: "Agents" },
  { key: "human", label: "People" },
  { key: "system", label: "System" },
];

export function Audit() {
  const { data, isPending } = useAudit();
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");

  const events = useMemo(() => (data ?? []).filter((e) => filter === "all" || e.actorKind === filter), [data, filter]);

  const { data: agents, error: agentsError } = useAgents();

  return (
    <>
      <PageHeader title="Audit trail">
        <p>Every agent step, human decision and system action, with the evidence it relied on. Agent reasoning is summarised, never shown in full.</p>
      </PageHeader>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Panel className="p-4 sm:p-5">
          <div role="tablist" aria-label="Filter events" className="mb-5 flex w-fit flex-wrap gap-1 rounded-full bg-ink/[0.05] p-1">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                role="tab"
                aria-selected={filter === f.key}
                onClick={() => setFilter(f.key)}
                className={clsx(
                  "rounded-full px-3 py-1.5 text-sm font-semibold transition-colors",
                  filter === f.key ? "bg-white text-ink shadow-sm" : "text-slate hover:text-ink",
                )}
              >
                {f.label}
              </button>
            ))}
          </div>
          {isPending ? <Skeleton className="h-80" /> : <Timeline events={events} showWorkflow />}
        </Panel>

        <Panel className="h-fit p-4 sm:p-5">
          <h2 className="text-lg font-bold text-ink">Agents and checks</h2>
          <p className="mt-1 text-sm text-slate">Every node run is recorded. &ldquo;Fallback&rdquo; means the LLM was unavailable or its output was rejected, and the deterministic version ran instead.</p>
          {agentsError ? (
            <p className="mt-4 text-sm text-slate">{agentsError.message}</p>
          ) : (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full min-w-[420px] text-left text-sm">
                <thead className="text-xs text-faint">
                  <tr>
                    <th className="pb-2 pr-3 font-semibold">Agent</th>
                    <th className="pb-2 pr-3 text-right font-semibold">Runs</th>
                    <th className="pb-2 pr-3 text-right font-semibold">LLM / fallback</th>
                    <th className="pb-2 pr-3 text-right font-semibold">Avg</th>
                    <th className="pb-2 text-right font-semibold">Tokens</th>
                  </tr>
                </thead>
                <tbody className="tnum divide-y divide-white/70">
                  {(agents ?? []).map((a) => (
                    <tr key={a.agent}>
                      <td className="py-2.5 pr-3 font-semibold text-ink">
                        {a.agent}
                        {a.errors > 0 && <span className="ml-1.5 text-xs font-normal text-coral">{a.errors} errors</span>}
                      </td>
                      <td className="py-2.5 pr-3 text-right text-slate">{a.calls}</td>
                      <td className="py-2.5 pr-3 text-right text-slate">
                        {a.llmCalls + a.fallbackCalls > 0 ? `${a.llmCalls} / ${a.fallbackCalls}` : "rules"}
                      </td>
                      <td className="py-2.5 pr-3 text-right text-slate">{a.avgLatencyMs >= 1000 ? `${(a.avgLatencyMs / 1000).toFixed(1)}s` : `${Math.round(a.avgLatencyMs)}ms`}</td>
                      <td className="py-2.5 text-right text-slate">{a.tokens.toLocaleString("en-IN")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-4 text-xs leading-snug text-faint">LLM prompts are redacted before they leave the server. Set LANGSMITH_TRACING to see full traces.</p>
        </Panel>
      </div>
    </>
  );
}
