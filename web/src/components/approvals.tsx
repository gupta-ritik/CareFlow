"use client";

import clsx from "clsx";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { EvidenceRef, PageHeader, Panel, RiskTag, Skeleton } from "./ui";
import type { Decision } from "@/lib/api";
import { ACTION_SHORT, DEPT_LABEL, since } from "@/lib/format";
import { useActions, useDecide, useWorkflows } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { ProposedAction, Workflow } from "@/lib/types";

const DONE_LABEL = ACTION_SHORT;

const RESULT: Record<Decision, string> = {
  approve: "Approved. The workflow resumed from its checkpoint; the action ran and Verify re-read the source system.",
  reject: "Rejected. The workflow stays paused and the blocker remains open.",
  request_information: "Information requested. The Resolution Planner will gather more evidence before asking again.",
};

function Detail({ action, wf }: { action: ProposedAction; wf?: Workflow }) {
  const role = useSession((st) => st.user?.role ?? "");
  const decide = useDecide();
  const [comment, setComment] = useState("");
  const [result, setResult] = useState<string | null>(null);

  const blocker = wf?.blockers.find((b) => b.id === action.blockerId);
  const evidence = wf?.evidence.filter((e) => action.evidenceIds.includes(e.id)) ?? [];
  const canDecide = action.status === "pending" && (role === action.approverRole || role === "Administrator");

  const submit = (decision: Decision) => {
    setResult(null);
    decide.mutate(
      { id: action.id, decision, comment },
      {
        onSuccess: () => {
          setResult(RESULT[decision]);
          setComment("");
        },
      },
    );
  };

  return (
    <Panel strong className="p-5 sm:p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <code className="text-sm font-semibold text-harbor">{action.action}</code>
        <RiskTag risk={action.risk} />
      </div>
      <h2 className="mt-2 text-xl font-bold leading-snug tracking-[-0.01em] text-ink">{action.description}</h2>
      {wf && (
        <p className="tnum mt-1 text-sm text-slate">
          For{" "}
          <Link href={`/workflows/${wf.id}`} className="font-semibold text-harbor hover:underline">
            {wf.patient.displayName}
          </Link>
          , {wf.patient.ward} {wf.patient.bed}. Proposed {since(action.createdAt)}.
        </p>
      )}

      <dl className="mt-5 grid gap-4 sm:grid-cols-2">
        <div className="rounded-2xl bg-white/55 p-4">
          <dt className="text-xs font-semibold text-slate">Why</dt>
          <dd className="mt-1 text-sm leading-snug text-ink">
            {blocker ? (
              <>
                <span className="font-semibold">{DEPT_LABEL[blocker.department]}:</span> {blocker.summary}
              </>
            ) : (
              "The blocker this action addressed is closed."
            )}
          </dd>
        </div>
        <div className="rounded-2xl bg-white/55 p-4">
          <dt className="text-xs font-semibold text-slate">Expected result</dt>
          <dd className="mt-1 text-sm leading-snug text-ink">{action.expectedImpact}</dd>
        </div>
      </dl>

      <div className="mt-4">
        <p className="text-xs font-semibold text-slate">Evidence the agent cited</p>
        <ul className="mt-2 flex flex-col gap-2">
          {evidence.map((e) => (
            <li key={e.id} className="grid gap-1 rounded-xl border border-white/80 bg-white/45 px-3 py-2.5 text-sm sm:grid-cols-[auto_minmax(0,1fr)] sm:gap-3">
              <EvidenceRef id={e.id} />
              <div className="min-w-0">
                <p className="text-ink">{e.observed}</p>
                <p className="mt-0.5 truncate text-xs text-faint">
                  {e.source}: <code className="text-slate">{e.query}</code>
                </p>
              </div>
            </li>
          ))}
        </ul>
      </div>

      {action.sopRefs.length > 0 && (
        <p className="mt-4 text-xs text-slate">
          Procedure cited: <span className="font-semibold text-ink">{action.sopRefs.join("; ")}</span>
        </p>
      )}

      <div className="mt-6 border-t border-white/80 pt-5">
        {action.status !== "pending" ? (
          <p className="text-sm text-slate">
            <span className="font-semibold text-ink">{DONE_LABEL[action.status]}</span>
            {action.decidedBy && ` by ${action.decidedBy}, ${since(action.decidedAt!)}`}
            {action.comment && `: "${action.comment}"`}
            {!action.requiresApproval && ". The policy check allowed this without a reviewer."}
          </p>
        ) : !canDecide ? (
          <p className="text-sm text-slate">
            Only a <span className="font-semibold text-ink">{action.approverRole}</span> can decide this. You&apos;re viewing as {role}; sign in as that role to decide it.
          </p>
        ) : (
          <>
            <label htmlFor="comment" className="text-sm font-semibold text-ink">
              Comment for the audit trail
            </label>
            <textarea
              id="comment"
              rows={2}
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Optional. Required context if you reject or ask for more information."
              className="mt-2 w-full resize-y rounded-xl border border-white/80 bg-white/70 px-3 py-2 text-sm text-ink placeholder:text-faint"
            />
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={decide.isPending}
                onClick={() => submit("approve")}
                className="rounded-xl bg-tide px-4 py-2.5 text-sm font-semibold text-white hover:bg-[#0b7b73] disabled:opacity-60"
              >
                Approve action
              </button>
              <button
                type="button"
                disabled={decide.isPending || !comment.trim()}
                onClick={() => submit("request_information")}
                className="rounded-xl border border-ink/15 bg-white/70 px-4 py-2.5 text-sm font-semibold text-ink hover:bg-white disabled:opacity-50"
              >
                Ask for more information
              </button>
              <button
                type="button"
                disabled={decide.isPending || !comment.trim()}
                onClick={() => submit("reject")}
                className="rounded-xl px-4 py-2.5 text-sm font-semibold text-coral hover:bg-coral-soft disabled:opacity-50"
              >
                Reject
              </button>
            </div>
          </>
        )}
        <p aria-live="polite" className={clsx("text-sm", result && "mt-3 rounded-xl bg-tide-soft px-3 py-2 text-[#0a6b64]")}>
          {result}
        </p>
        {decide.error && <p className="mt-3 text-sm text-coral">{decide.error.message}</p>}
      </div>
    </Panel>
  );
}

export function Approvals() {
  const { data: actions, isPending } = useActions();
  const { data: workflows } = useWorkflows();
  const params = useSearchParams();
  const router = useRouter();

  const sorted = [...(actions ?? [])].sort(
    (a, b) => Number(b.status === "pending") - Number(a.status === "pending") || b.createdAt.localeCompare(a.createdAt),
  );
  const selectedId = params.get("id") ?? sorted[0]?.id;
  const selected = sorted.find((a) => a.id === selectedId);
  const pending = sorted.filter((a) => a.status === "pending").length;

  return (
    <>
      <PageHeader title="Approvals">
        {isPending ? (
          <Skeleton className="h-5 w-2/3" />
        ) : (
          <p className="tnum">
            {pending === 0 ? "Nothing is waiting on a reviewer." : `${pending} actions are paused until a reviewer decides.`} Low-risk actions run without a reviewer and still appear here for the record.
          </p>
        )}
      </PageHeader>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.6fr)]">
        <Panel className="p-2">
          {isPending ? (
            <div className="flex flex-col gap-2 p-2">
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
            </div>
          ) : (
            <ul className="flex flex-col gap-1" aria-label="Approval requests">
              {sorted.map((a) => {
                const wf = workflows?.find((w) => w.id === a.workflowId);
                const active = a.id === selected?.id;
                return (
                  <li key={a.id}>
                    <button
                      type="button"
                      onClick={() => router.replace(`/approvals?id=${a.id}`, { scroll: false })}
                      aria-current={active ? "true" : undefined}
                      className={clsx(
                        "w-full rounded-2xl px-4 py-3 text-left transition-colors",
                        active ? "bg-white/80 shadow-[0_6px_16px_-8px_rgba(22,60,98,.25)]" : "hover:bg-white/45",
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-sm font-semibold text-ink">{wf?.patient.displayName ?? a.workflowId}</p>
                        <span
                          className={clsx(
                            "shrink-0 text-xs font-semibold",
                            a.status === "pending" ? "text-[#4b3d93]" : "text-faint",
                          )}
                        >
                          {DONE_LABEL[a.status]}
                        </span>
                      </div>
                      <p className="mt-0.5 line-clamp-2 text-sm leading-snug text-slate">{a.description}</p>
                      <p className="tnum mt-1 text-xs text-faint">
                        {a.approverRole}, {since(a.createdAt)}
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>

        {selected ? (
          <Detail key={selected.id} action={selected} wf={workflows?.find((w) => w.id === selected.workflowId)} />
        ) : (
          !isPending && (
            <Panel className="p-6 text-sm text-slate">Select a request to see its evidence and decide.</Panel>
          )
        )}
      </div>
    </>
  );
}
