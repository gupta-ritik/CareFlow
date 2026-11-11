"use client";

import clsx from "clsx";
import { AlertTriangle, Check, CheckCircle2, CircleDashed, ClipboardCheck, FileSearch, Inbox, Search, ShieldCheck, X as XIcon } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  EvidenceRef,
  PageHeader,
  Panel,
  RiskTag,
  Skeleton,
  StatCard,
  Tabs,
  inputClass,
  toast,
  type Tone,
} from "./ui";
import type { Decision } from "@/lib/api";
import { ACTION_LABEL, ACTION_SHORT, DEPT_LABEL, clock, since } from "@/lib/format";
import { useActions, useAudit, useDecide, useWorkflows } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { ActionStatus, ProposedAction, Workflow } from "@/lib/types";

/** Roles the backend lets decide any approval, whatever its approver role (security.APPROVE_ANY). */
const APPROVE_ANY = ["Administrator", "Hospital director"];

export const STATUS_TONE: Record<ActionStatus, Tone> = {
  pending: "violet",
  approved: "info",
  auto_approved: "info",
  executed: "info",
  info_requested: "warning",
  verified: "success",
  rejected: "neutral",
  superseded: "neutral",
  verification_failed: "danger",
  execution_failed: "danger",
};

const IN_FLIGHT: ActionStatus[] = ["approved", "auto_approved", "executed", "info_requested"];
const DONE: ActionStatus[] = ["verified", "rejected", "superseded"];
const FAILED: ActionStatus[] = ["execution_failed", "verification_failed"];

type Queue = "mine" | "pending" | "flight" | "done" | "failed" | "all";

const label = (action: string) => action.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

function canDecideAs(role: string, a: ProposedAction) {
  return a.status === "pending" && (role === a.approverRole || APPROVE_ANY.includes(role));
}

/* ---- execution lifecycle ---------------------------------------------------------------------------------------- */
type StepState = "done" | "current" | "failed" | "skipped" | "todo";

function lifecycle(a: ProposedAction): { title: string; state: StepState; note: string }[] {
  const s = a.status;
  const approval: { state: StepState; note: string } = !a.requiresApproval
    ? { state: "done", note: "Not required: the policy check allows this low-risk action without a reviewer." }
    : s === "pending"
      ? { state: "current", note: `Waiting for a ${a.approverRole.toLowerCase()}.` }
      : s === "rejected"
        ? { state: "failed", note: `Rejected${a.decidedBy ? ` by ${a.decidedBy}` : ""}. The blocker stays open.` }
        : s === "info_requested"
          ? { state: "current", note: "More information requested. The planner gathers evidence and proposes again." }
          : { state: "done", note: `Approval recorded${a.decidedBy ? ` by ${a.decidedBy}` : ""}${a.decidedAt ? `, ${since(a.decidedAt)}` : ""}.` };
  const stopped = ["pending", "rejected", "info_requested"].includes(s);
  const execution: { state: StepState; note: string } = stopped
    ? { state: "todo", note: "Runs only after approval." }
    : s === "approved" || s === "auto_approved"
      ? { state: "current", note: "Execution started in the department system." }
      : s === "execution_failed"
        ? { state: "failed", note: "The department system refused or couldn't be reached. Nothing was changed." }
        : s === "superseded"
          ? { state: "skipped", note: "Skipped: the blocker was already gone, so nothing was done." }
          : { state: "done", note: "The action completed in the department system." };
  const verification: { state: StepState; note: string } =
    s === "verified"
      ? { state: "done", note: "Verify re-read the source system and found the change." }
      : s === "verification_failed"
        ? { state: "failed", note: "Verify re-read the source system and did not find the change." }
        : s === "executed"
          ? { state: "current", note: "Re-reading the source system to confirm the change." }
          : s === "superseded" || s === "execution_failed" || stopped
            ? { state: s === "superseded" ? "skipped" : "todo", note: s === "superseded" ? "Not needed." : "Happens after execution." }
            : { state: "todo", note: "Happens after execution." };
  return [
    { title: "Proposed by the Resolution Planner", state: "done", note: `${since(a.createdAt)}, ${clock(a.createdAt)}.` },
    { title: "Approval", ...approval },
    { title: "Execution", ...execution },
    { title: "Source-system verification", ...verification },
  ];
}

function Lifecycle({ action }: { action: ProposedAction }) {
  const icon: Record<StepState, React.ReactNode> = {
    done: <CheckCircle2 className="size-4 text-success" aria-hidden />,
    current: <CircleDashed className="size-4 animate-[spin_3s_linear_infinite] text-harbor motion-reduce:animate-none" aria-hidden />,
    failed: <XIcon className="size-4 text-coral-ink" aria-hidden />,
    skipped: <span className="block size-2 rounded-full bg-disabled" aria-hidden />,
    todo: <span className="block size-2 rounded-full border border-line-strong" aria-hidden />,
  };
  const word: Record<StepState, string> = { done: "Done", current: "In progress", failed: "Failed", skipped: "Skipped", todo: "Not started" };
  return (
    <ol className="flex flex-col" aria-label="Execution status">
      {lifecycle(action).map((step, i, all) => (
        <li key={step.title} className="relative flex gap-3 pb-5 last:pb-0">
          {i < all.length - 1 && <span className="absolute left-[11px] top-6 h-[calc(100%-18px)] w-px bg-line" aria-hidden />}
          <span className="grid size-6 shrink-0 place-items-center rounded-full bg-surface ring-1 ring-line">{icon[step.state]}</span>
          <div className="min-w-0 pt-0.5">
            <p className="text-sm font-semibold text-ink">
              {step.title} <span className="sr-only">: {word[step.state]}</span>
            </p>
            <p className="mt-0.5 text-[13px] leading-relaxed text-slate">{step.note}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/* ---- detail ------------------------------------------------------------------------------------------------------ */
type Tab = "details" | "evidence" | "execution" | "activity";

function Detail({ action, wf }: { action: ProposedAction; wf?: Workflow }) {
  const role = useSession((st) => st.user?.role ?? "");
  const decide = useDecide();
  const audit = useAudit(action.workflowId);
  const [tab, setTab] = useState<Tab>(action.status === "pending" ? "details" : "execution");
  const [comment, setComment] = useState("");
  const [confirm, setConfirm] = useState<Decision | null>(null);

  const blocker = wf?.blockers.find((b) => b.id === action.blockerId);
  const evidence = wf?.evidence.filter((e) => action.evidenceIds.includes(e.id)) ?? [];
  const canDecide = canDecideAs(role, action);
  const activity = (audit.data ?? []).filter(
    (e) => e.detail.includes(action.id) || e.event.includes(action.id) || e.event.toLowerCase().includes(action.action.replace(/_/g, " ")),
  );

  const submit = (decision: Decision) => {
    decide.mutate(
      { id: action.id, decision, comment },
      {
        onSuccess: (updated) => {
          setConfirm(null);
          setComment("");
          setTab("execution");
          // Report what the server says happened, not what we hope happened.
          toast.success(`${action.id}: ${ACTION_LABEL[updated.status]}.`);
        },
      },
    );
  };

  return (
    <div id="approval-detail" className="scroll-mt-20">
      <Panel strong className="flex min-h-0 flex-col">
        <div className="border-b border-line px-5 pb-4 pt-5 sm:px-6">
          <div className="flex flex-wrap items-center gap-2">
            <span className="tnum font-mono text-xs font-semibold text-faint">{action.id}</span>
            <Badge tone={STATUS_TONE[action.status]}>{ACTION_SHORT[action.status]}</Badge>
            <RiskTag risk={action.risk} />
            {blocker && <Badge dot={false}>{DEPT_LABEL[blocker.department]}</Badge>}
          </div>
          <h2 className="mt-3 text-[19px] font-semibold leading-snug tracking-[-0.01em] text-ink">{action.description}</h2>
          <p className="tnum mt-1.5 text-[13px] text-slate">
            {wf ? (
              <>
                For{" "}
                <Link href={`/workflows/${wf.id}`} className="font-semibold text-tide-deep hover:underline">
                  {wf.patient.displayName}
                </Link>
                , {[wf.patient.ward, wf.patient.bed].filter(Boolean).join(" ")} ·{" "}
              </>
            ) : (
              <>{action.workflowId} · </>
            )}
            proposed {since(action.createdAt)} · reviewer: {action.requiresApproval ? action.approverRole : "none (low risk)"}
          </p>
          <Tabs<Tab>
            className="mt-4"
            label="Request details"
            value={tab}
            onChange={setTab}
            options={[
              { value: "details", label: "Details" },
              { value: "evidence", label: "Evidence", count: evidence.length },
              { value: "execution", label: "Execution" },
              { value: "activity", label: "Activity" },
            ]}
          />
        </div>

        <div className="flex-1 px-5 py-5 sm:px-6" role="tabpanel">
          {tab === "details" && (
            <div className="flex flex-col gap-4">
              <dl className="grid gap-3 sm:grid-cols-2">
                <div className="rounded-xl border border-line bg-surface-2 p-4">
                  <dt className="text-xs font-semibold uppercase tracking-[0.06em] text-faint">Why it was proposed</dt>
                  <dd className="mt-1.5 text-sm leading-relaxed text-ink">
                    {blocker ? (
                      <>
                        <span className="font-semibold">{DEPT_LABEL[blocker.department]}:</span> {blocker.summary}
                      </>
                    ) : (
                      "The blocker this action addressed is closed."
                    )}
                  </dd>
                </div>
                <div className="rounded-xl border border-line bg-surface-2 p-4">
                  <dt className="text-xs font-semibold uppercase tracking-[0.06em] text-faint">Expected result</dt>
                  <dd className="mt-1.5 text-sm leading-relaxed text-ink">{action.expectedImpact}</dd>
                </div>
              </dl>
              <div className="grid gap-3 text-sm sm:grid-cols-3">
                <div>
                  <p className="text-xs text-faint">Requested action</p>
                  <p className="mt-0.5 font-medium text-ink">{label(action.action)}</p>
                </div>
                <div>
                  <p className="text-xs text-faint">Risk classification</p>
                  <p className="mt-0.5 font-medium capitalize text-ink">{action.risk}</p>
                </div>
                <div>
                  <p className="text-xs text-faint">Procedure cited</p>
                  <p className="mt-0.5 font-medium text-ink">{action.sopRefs.length ? action.sopRefs.join("; ") : "None"}</p>
                </div>
              </div>
              <p className="rounded-xl bg-harbor-soft/60 px-3.5 py-2.5 text-[13px] leading-relaxed text-ink">
                Administrative coordination only. Approving never changes a clinical decision, medicine or claim outcome.
              </p>
            </div>
          )}

          {tab === "evidence" && (
            <div>
              {evidence.length === 0 ? (
                <EmptyState icon={FileSearch} title="No evidence attached">
                  The workflow&apos;s evidence isn&apos;t loaded, or the agent cited none.
                </EmptyState>
              ) : (
                <ul className="flex flex-col gap-2">
                  {evidence.map((e) => (
                    <li key={e.id} className="rounded-xl border border-line bg-surface-2 px-3.5 py-3 text-sm">
                      <div className="flex flex-wrap items-center gap-2">
                        <EvidenceRef id={e.id} />
                        <span className="text-xs text-faint">
                          {e.source} · {clock(e.observedAt)}
                        </span>
                        {e.untrusted && <Badge tone="warning">Untrusted text, ignored as instructions</Badge>}
                      </div>
                      <p className="mt-1.5 text-ink">{e.observed}</p>
                      <code className="mt-1 block truncate text-xs text-slate">{e.query}</code>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {tab === "execution" && <Lifecycle action={action} />}

          {tab === "activity" && (
            <div>
              {audit.isPending ? (
                <Skeleton className="h-24" />
              ) : activity.length === 0 ? (
                <EmptyState icon={Inbox} title="No activity recorded yet">
                  Decisions, execution and verification for this request appear here.
                </EmptyState>
              ) : (
                <ol className="flex flex-col divide-y divide-line">
                  {activity.map((e) => (
                    <li key={e.id} className="flex gap-3 py-2.5 text-sm">
                      <span className="tnum w-14 shrink-0 text-xs text-faint">{clock(e.at)}</span>
                      <div className="min-w-0">
                        <p className="font-medium text-ink">{e.event}</p>
                        <p className="text-[13px] text-slate">
                          {e.actor}
                          {e.detail && ` · ${e.detail}`}
                        </p>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
        </div>

        <div className="border-t border-line bg-surface-2/60 px-5 py-4 sm:px-6">
          {action.status !== "pending" ? (
            <p className="text-[13px] text-slate">
              <span className="font-semibold text-ink">{ACTION_LABEL[action.status]}</span>
              {action.decidedBy && ` · ${action.decidedBy}, ${since(action.decidedAt!)}`}
              {action.comment && ` · “${action.comment}”`}
            </p>
          ) : !canDecide ? (
            <p className="flex items-start gap-2 text-[13px] text-slate">
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-faint" aria-hidden />
              <span>
                Only a <span className="font-semibold text-ink">{action.approverRole}</span> (or an administrator or hospital director) can decide this.
                You&apos;re signed in as {role}.
              </span>
            </p>
          ) : (
            <>
              <label htmlFor="comment" className="text-[13px] font-semibold text-ink">
                Comment for the audit trail
              </label>
              <textarea
                id="comment"
                rows={2}
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder="Optional to approve. Required to reject or ask for more information."
                className={clsx(inputClass, "mt-1.5 h-auto resize-y py-2")}
              />
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="good" onClick={() => setConfirm("approve")} disabled={decide.isPending}>
                  <Check className="size-4" aria-hidden /> Approve
                </Button>
                <Button
                  variant="secondary"
                  disabled={decide.isPending || !comment.trim()}
                  onClick={() => submit("request_information")}
                  loading={decide.isPending && confirm === null}
                >
                  Request information
                </Button>
                <Button variant="danger" disabled={decide.isPending || !comment.trim()} onClick={() => setConfirm("reject")}>
                  Reject
                </Button>
              </div>
              {!comment.trim() && <p className="mt-2 text-xs text-faint">Add a comment to reject or request information.</p>}
            </>
          )}
        </div>

        <ConfirmDialog
          open={confirm !== null}
          onClose={() => setConfirm(null)}
          onConfirm={() => confirm && submit(confirm)}
          busy={decide.isPending}
          error={decide.error}
          tone={confirm === "reject" ? "dangerSolid" : "good"}
          title={confirm === "reject" ? "Reject this action?" : "Approve this action?"}
          confirmLabel={confirm === "reject" ? "Reject action" : "Approve and run"}
        >
          {confirm === "reject" ? (
            <>The workflow stays paused and the blocker remains open. Your comment is recorded in the audit trail.</>
          ) : (
            <>
              CareFlow runs <span className="font-semibold text-ink">{label(action.action)}</span> in the department system, then re-reads that system to verify
              it. The Execution tab shows each step as it&apos;s confirmed.
            </>
          )}
        </ConfirmDialog>
      </Panel>
    </div>
  );
}

/* ---- page ------------------------------------------------------------------------------------------------------- */
export function Approvals() {
  const { data: actions, isPending } = useActions();
  const { data: workflows } = useWorkflows();
  const role = useSession((s) => s.user?.role ?? "");
  const params = useSearchParams();
  const router = useRouter();
  const [q, setQ] = useState("");
  const [queue, setQueue] = useState<Queue | null>(null);

  const all = useMemo(
    () => [...(actions ?? [])].sort((a, b) => Number(b.status === "pending") - Number(a.status === "pending") || b.createdAt.localeCompare(a.createdAt)),
    [actions],
  );
  const wfById = useMemo(() => new Map((workflows ?? []).map((w) => [w.id, w])), [workflows]);
  const counts = {
    mine: all.filter((a) => canDecideAs(role, a)).length,
    pending: all.filter((a) => a.status === "pending").length,
    flight: all.filter((a) => IN_FLIGHT.includes(a.status)).length,
    done: all.filter((a) => DONE.includes(a.status)).length,
    failed: all.filter((a) => FAILED.includes(a.status)).length,
    verified: all.filter((a) => a.status === "verified").length,
    skipped: all.filter((a) => a.status === "superseded").length,
    highRisk: all.filter((a) => a.status === "pending" && a.risk === "high").length,
  };
  const active: Queue = queue ?? (counts.mine ? "mine" : counts.pending ? "pending" : "all");

  const term = q.trim().toLowerCase();
  const shown = all.filter((a) => {
    const inQueue =
      active === "mine"
        ? canDecideAs(role, a)
        : active === "pending"
          ? a.status === "pending"
          : active === "flight"
            ? IN_FLIGHT.includes(a.status)
            : active === "done"
              ? DONE.includes(a.status)
              : active === "failed"
                ? FAILED.includes(a.status)
                : true;
    if (!inQueue) return false;
    if (!term) return true;
    const wf = wfById.get(a.workflowId);
    return [a.id, a.workflowId, a.action.replace(/_/g, " "), a.description, wf?.patient.displayName, wf?.patient.mrn].some((s) =>
      s?.toLowerCase().includes(term),
    );
  });

  const selectedId = params.get("id") ?? shown[0]?.id;
  const selected = all.find((a) => a.id === selectedId);

  return (
    <>
      <PageHeader title="Approvals">
        Actions the agents proposed. Medium- and high-risk actions wait here for the right reviewer; low-risk ones run automatically and are listed for the
        record.
      </PageHeader>

      <div className="mb-5 grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard
          label="Waiting for you"
          value={isPending ? "–" : counts.mine}
          icon={ClipboardCheck}
          tone="violet"
          help="Pending requests your role can decide."
        />
        <StatCard
          label="Pending"
          value={isPending ? "–" : counts.pending}
          icon={CircleDashed}
          tone="info"
          help="All requests paused until a reviewer decides."
        />
        <StatCard
          label="Verified"
          value={isPending ? "–" : counts.verified}
          icon={CheckCircle2}
          tone="success"
          help="Executed, and Verify found the change in the source system."
        />
        <StatCard label="Skipped" value={isPending ? "–" : counts.skipped} icon={Inbox} help="Not run because the blocker was already gone." />
        <StatCard
          label="At risk"
          value={isPending ? "–" : counts.failed + counts.highRisk}
          icon={AlertTriangle}
          tone="danger"
          help="Failed executions or verifications, plus pending high-risk requests."
          className="col-span-2 lg:col-span-1"
        />
      </div>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[minmax(320px,0.85fr)_minmax(0,1.5fr)]">
        <Panel className="flex min-h-0 flex-col lg:sticky lg:top-[4.5rem] lg:max-h-[calc(100vh-6rem)]">
          <div className="flex flex-col gap-3 border-b border-line p-3">
            <Tabs<Queue>
              wrap
              label="Queue"
              value={active}
              onChange={(v) => {
                setQueue(v);
                router.replace("/approvals", { scroll: false });
              }}
              options={[
                { value: "mine", label: "For me", count: counts.mine },
                { value: "pending", label: "Pending", count: counts.pending },
                { value: "flight", label: "In flight", count: counts.flight },
                { value: "done", label: "Done", count: counts.done },
                { value: "failed", label: "Failed", count: counts.failed },
                { value: "all", label: "All" },
              ]}
            />
            <div className="relative">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
              <input
                type="search"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Patient, request or workflow ID, action"
                aria-label="Filter requests"
                className={clsx(inputClass, "pl-9")}
              />
            </div>
          </div>
          {isPending ? (
            <div className="flex flex-col gap-2 p-3">
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
              <Skeleton className="h-20" />
            </div>
          ) : shown.length === 0 ? (
            <EmptyState icon={ClipboardCheck} title={active === "mine" ? "Nothing waiting for you" : "No requests here"}>
              {term
                ? "Try a different search."
                : active === "mine"
                  ? "New requests for your role will appear here, and in your notifications."
                  : "Pick another queue above."}
            </EmptyState>
          ) : (
            <ul className="scroll-quiet flex flex-col gap-1 overflow-y-auto p-2" aria-label="Approval requests">
              {shown.map((a) => {
                const wf = wfById.get(a.workflowId);
                const isSel = a.id === selected?.id;
                return (
                  <li key={a.id}>
                    <button
                      type="button"
                      onClick={() => {
                        router.replace(`/approvals?id=${a.id}`, { scroll: false });
                        // On phones the details sit below the list: bring them into view.
                        if (window.matchMedia("(max-width: 1023px)").matches) {
                          setTimeout(() => document.getElementById("approval-detail")?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
                        }
                      }}
                      aria-current={isSel ? "true" : undefined}
                      className={clsx(
                        "w-full rounded-[11px] border px-3.5 py-3 text-left transition-colors",
                        isSel ? "border-tide/40 bg-tide-soft/40 ring-1 ring-tide/20" : "border-transparent hover:bg-hover",
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="truncate text-sm font-semibold text-ink">{wf?.patient.displayName ?? a.workflowId}</p>
                        <Badge tone={STATUS_TONE[a.status]}>{ACTION_SHORT[a.status]}</Badge>
                      </div>
                      <p className="mt-1 line-clamp-2 text-[13px] leading-snug text-slate">{label(a.action)}</p>
                      <p className="tnum mt-1.5 flex flex-wrap items-center gap-x-2 text-xs text-faint">
                        <span className="font-mono">{a.id}</span>
                        <span aria-hidden>·</span>
                        <span className={clsx(a.risk === "high" && "font-semibold text-coral-ink")}>{a.risk} risk</span>
                        <span aria-hidden>·</span>
                        <span>{since(a.createdAt)}</span>
                      </p>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Panel>

        {selected ? (
          <Detail key={selected.id} action={selected} wf={wfById.get(selected.workflowId)} />
        ) : (
          !isPending && (
            <Panel className="grid place-items-center">
              <EmptyState icon={ClipboardCheck} title="Select a request">
                Its evidence, execution status and decision controls appear here.
              </EmptyState>
            </Panel>
          )
        )}
      </div>
    </>
  );
}
