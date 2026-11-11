import clsx from "clsx";
import { Bot, Cog, UserRound } from "lucide-react";
import type { ActorKind, AuditEvent } from "@/lib/types";
import { clock, since } from "@/lib/format";
import { EvidenceRef } from "./ui";

const ACTOR_ICON: Record<ActorKind, typeof Bot> = { agent: Bot, human: UserRound, system: Cog };
const ACTOR_TONE: Record<ActorKind, string> = {
  agent: "bg-harbor-soft text-harbor",
  human: "bg-violet-soft text-violet",
  system: "bg-ink/[0.06] text-slate",
};

export function Timeline({ events, showWorkflow }: { events: AuditEvent[]; showWorkflow?: boolean }) {
  if (events.length === 0) return <p className="text-sm text-slate">Nothing has happened on this workflow yet.</p>;

  return (
    <ol className="relative flex flex-col">
      {events.map((e, i) => {
        const Icon = ACTOR_ICON[e.actorKind];
        return (
          <li key={e.id} className="relative flex gap-3 pb-5 last:pb-0">
            {i < events.length - 1 && <span className="absolute left-[15px] top-8 bottom-0 w-px bg-ink/10" aria-hidden />}
            <span className={clsx("relative mt-0.5 grid size-8 shrink-0 place-items-center rounded-full", ACTOR_TONE[e.actorKind])}>
              <Icon className="size-4" aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                <p className="text-sm font-semibold text-ink">
                  {e.event}
                  {showWorkflow && <span className="tnum font-normal text-faint"> in {e.workflowId}</span>}
                </p>
                <time dateTime={e.at} className="tnum text-xs text-faint" title={since(e.at)}>
                  {clock(e.at)}
                </time>
              </div>
              <p className="text-sm text-slate">
                <span className="font-medium text-ink/80">{e.actor}</span>. {e.detail}
              </p>
              {(e.evidenceIds?.length || e.latencyMs != null) && (
                <div className="tnum mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-faint">
                  {e.evidenceIds?.map((id) => <EvidenceRef key={id} id={id} />)}
                  {e.latencyMs != null && <span>{e.latencyMs >= 1000 ? `${(e.latencyMs / 1000).toFixed(1)}s` : `${e.latencyMs}ms`}</span>}
                  {e.tokens != null && <span>{e.tokens.toLocaleString("en-IN")} tokens</span>}
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
