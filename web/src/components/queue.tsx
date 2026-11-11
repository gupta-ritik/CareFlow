"use client";

import clsx from "clsx";
import { AlertTriangle, ArrowUpRight, Bell, CheckCircle2, CircleDashed, ListTodo, Loader } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { Badge, Button, EmptyState, ErrorText, PageHeader, Panel, Skeleton, StatCard, Tabs, inputClass, type Tone } from "./ui";
import { DEPT_LABEL, KIND_LABEL, TASK_LABEL, duration, minutesSince, since } from "@/lib/format";
import { useDepartmentAction, useMyDepartments, useQueue } from "@/lib/queries";
import type { Department, QueueItem, TaskStatus } from "@/lib/types";

const TASK_TONE: Record<TaskStatus, Tone> = { cleared: "success", in_progress: "info", blocked: "warning", not_started: "neutral" };

const label = (s: string) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

type Act = (action: string, params?: Record<string, unknown>) => void;

function Documentation({ item, act, busy }: { item: QueueItem; act: Act; busy: boolean }) {
  const required = (item.record.required as string[]) ?? [];
  const [present, setPresent] = useState<string[]>((item.record.present as string[]) ?? []);
  const saved = (item.record.present as string[]) ?? [];
  const dirty = present.length !== saved.length || present.some((p) => !saved.includes(p));
  return (
    <div>
      <p className="text-sm text-slate">
        Discharge summary <span className="tnum">{String(item.record.doc_id)}</span>: {saved.length} of {required.length} required fields complete
      </p>
      <fieldset className="mt-2 grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
        <legend className="sr-only">Completed fields</legend>
        {required.map((f) => (
          <label key={f} className="flex items-center gap-2 text-sm text-ink">
            <input
              type="checkbox"
              className="size-4"
              checked={present.includes(f)}
              onChange={(e) => setPresent(e.target.checked ? [...present, f] : present.filter((x) => x !== f))}
            />
            {label(f)}
          </label>
        ))}
      </fieldset>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button disabled={busy || !dirty} onClick={() => act("update_fields", { present })}>
          Save document fields
        </Button>
        <Button variant="secondary" disabled={busy} onClick={() => setPresent(required)}>
          Tick all
        </Button>
      </div>
    </div>
  );
}

function Pharmacy({ item, act, busy }: { item: QueueItem; act: Act; busy: boolean }) {
  const r = item.record;
  const [name, setName] = useState("");
  return (
    <div>
      <p className="text-sm text-slate">
        Task {String(r.task_id)}: {label(String(r.status).toLowerCase())}
        {r.assigned_to ? `, assigned to ${r.assigned_to}` : ", no pharmacist assigned"}. Reconciliation {String(r.reconciliation).toLowerCase()}.
        {typeof r.ordered_at === "string" && ` Ordered ${since(r.ordered_at)}.`}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor={`ph-${item.patientRef}`} className="sr-only">
          Pharmacist
        </label>
        <input id={`ph-${item.patientRef}`} value={name} onChange={(e) => setName(e.target.value)} placeholder="Pharmacist name" className={clsx(inputClass, "w-48")} />
        <Button variant="secondary" disabled={busy || !name.trim()} onClick={() => act("assign", { pharmacist: name })}>
          Assign
        </Button>
        <Button disabled={busy || r.status === "DONE"} onClick={() => act("mark_ready")}>
          Medicines ready
        </Button>
        <Button disabled={busy || r.reconciliation === "DONE"} onClick={() => act("complete_reconciliation")}>
          Reconciliation complete
        </Button>
      </div>
    </div>
  );
}

function Laboratory({ item, act, busy }: { item: QueueItem; act: Act; busy: boolean }) {
  const reports = (item.record.reports as { id: string; status: string; acknowledged: boolean }[]) ?? [];
  const [id, setId] = useState("");
  const [final, setFinal] = useState(true);
  return (
    <div>
      {reports.length === 0 ? (
        <p className="text-sm text-slate">No lab reports recorded for this stay.</p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {reports.map((rep) => (
            <li key={rep.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="tnum font-semibold text-ink">{rep.id}</span>
              <span className="text-slate">{rep.status === "final" ? (rep.acknowledged ? "final, acknowledged" : "final, not acknowledged") : "pending"}</span>
              {rep.status !== "final" && (
                <Button variant="secondary" disabled={busy} onClick={() => act("mark_final", { report_id: rep.id })}>
                  Mark final
                </Button>
              )}
              {rep.status === "final" && !rep.acknowledged && (
                <Button disabled={busy} onClick={() => act("acknowledge", { report_id: rep.id })}>
                  Ward acknowledged
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor={`lab-${item.patientRef}`} className="sr-only">
          Report ID
        </label>
        <input id={`lab-${item.patientRef}`} value={id} onChange={(e) => setId(e.target.value)} placeholder="Report ID" className={clsx(inputClass, "w-40")} />
        <label className="flex items-center gap-2 text-sm text-ink">
          <input type="checkbox" className="size-4" checked={final} onChange={(e) => setFinal(e.target.checked)} /> Final
        </label>
        <Button variant="secondary" disabled={busy || !id.trim()} onClick={() => (act("add_report", { report_id: id.trim(), final }), setId(""))}>
          Add report
        </Button>
      </div>
    </div>
  );
}

function Insurance({ item, act, busy }: { item: QueueItem; act: Act; busy: boolean }) {
  const r = item.record;
  const [doc, setDoc] = useState("");
  const [reason, setReason] = useState("");
  const status = String(r.status);
  const docs = (r.available_documents as string[]) ?? [];
  return (
    <div>
      <p className="text-sm text-slate">
        Claim {String(r.claim_id)}: {label(status.toLowerCase())}
        {typeof r.submitted_at === "string" && `, submitted ${since(r.submitted_at)}`}
        {status === "QUERY_RAISED" && r.requested_document ? `. Payer asked for: ${label(String(r.requested_document))}` : ""}
        {status === "DENIED" && r.denial_reason ? `. Reason: ${r.denial_reason}` : ""}.
      </p>
      {docs.length > 0 && <p className="mt-1 text-xs text-faint">Documents available for the claim: {docs.map(label).join(", ")}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button disabled={busy || status === "APPROVED"} onClick={() => act("submit")}>
          {status === "NOT_SUBMITTED" ? "Submit to payer" : "Resubmit"}
        </Button>
        <Button variant="good" disabled={busy || status === "APPROVED"} onClick={() => act("record_approval")}>
          Payer approved
        </Button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label htmlFor={`doc-${item.patientRef}`} className="sr-only">
          Document name
        </label>
        <input id={`doc-${item.patientRef}`} value={doc} onChange={(e) => setDoc(e.target.value)} placeholder="Document, e.g. itemised pharmacy bill" className={clsx(inputClass, "w-72")} />
        <Button variant="secondary" disabled={busy || !doc.trim()} onClick={() => (act("record_query", { document: doc }), setDoc(""))}>
          Payer asked for this
        </Button>
        <Button variant="secondary" disabled={busy || !doc.trim()} onClick={() => (act("add_document", { document: doc }), setDoc(""))}>
          We have this document
        </Button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <label htmlFor={`deny-${item.patientRef}`} className="sr-only">
          Denial reason
        </label>
        <input id={`deny-${item.patientRef}`} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Denial reason" className={clsx(inputClass, "w-56")} />
        <Button variant="danger" disabled={busy || !reason.trim()} onClick={() => (act("record_denial", { reason }), setReason(""))}>
          Payer denied
        </Button>
        {status === "DENIED" && (
          <Button variant="secondary" disabled={busy} onClick={() => act("convert_self_pay")}>
            Convert to self-pay
          </Button>
        )}
      </div>
    </div>
  );
}

function Billing({ item, act, busy }: { item: QueueItem; act: Act; busy: boolean }) {
  const s = String(item.record.status);
  return (
    <div>
      <p className="text-sm text-slate">
        Bill {String(item.record.bill_id)}: {label(s.toLowerCase())}.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="secondary" disabled={busy || s !== "NOT_STARTED"} onClick={() => act("start")}>
          Start final bill
        </Button>
        <Button disabled={busy || s === "FINAL"} onClick={() => act("finalize")}>
          Final bill ready
        </Button>
      </div>
    </div>
  );
}

const CONTROLS: Record<Department, typeof Billing> = { documentation: Documentation, pharmacy: Pharmacy, laboratory: Laboratory, insurance: Insurance, billing: Billing };

function Card({ dept, item, onTouched, canAct }: { dept: Department; item: QueueItem; onTouched: (ref: string) => void; canAct: boolean }) {
  const action = useDepartmentAction();
  const [done, setDone] = useState<string | null>(null);
  const Controls = CONTROLS[dept];
  const act: Act = (a, params) => {
    setDone(null);
    onTouched(item.patientRef); // keep this card on screen even if the update clears the department
    action.mutate({ dept, ref: item.patientRef, action: a, params }, { onSuccess: (r) => setDone(r.summary) });
  };
  const history = item.record.history ?? [];

  return (
    <Panel className={clsx("p-4 sm:p-5", item.blockers.length > 0 && "border-amber/50")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[16px] font-semibold text-ink">{item.displayName}</p>
          <p className="tnum text-sm text-slate">
            {item.ward}
            {item.bed && `, ${item.bed}`} <span className="text-faint">/ {item.mrn} / {item.payer}</span>
          </p>
        </div>
        <div className="text-right">
          {item.taskStatus ? (
            <Badge tone={TASK_TONE[item.taskStatus]}>{TASK_LABEL[item.taskStatus]}</Badge>
          ) : (
            <span className="text-sm text-faint">Discharge not authorized yet</span>
          )}
          {item.workflow && (
            <p className="tnum text-xs text-slate">
              <Link href={`/workflows/${item.workflow.id}`} className="font-semibold text-tide-deep hover:underline">
                {item.workflow.id}
              </Link>
              , authorized {duration(minutesSince(item.workflow.authorizedAt))} ago
            </p>
          )}
        </div>
      </div>

      {item.blockers.map((b) => (
        <p key={b.id} className="mt-3 rounded-xl border border-amber/40 bg-amber-soft/60 px-3 py-2 text-sm text-ink">
          <span className="font-semibold">{KIND_LABEL[b.kind] ?? label(b.kind)}:</span> {b.summary}
        </p>
      ))}
      {item.requests.slice(0, 3).map((r) => (
        <p key={`${r.ref}-${r.at}`} className="mt-2 flex items-start gap-2 rounded-xl bg-violet-soft/70 px-3 py-2 text-sm text-ink">
          <Bell className="mt-0.5 size-4 shrink-0 text-violet" aria-hidden />
          <span>
            <span className="font-semibold">CareFlow {r.kind.toLowerCase()}</span>
            {r.text && `: ${r.text}`} <span className="text-xs text-faint">({since(r.at)})</span>
          </span>
        </p>
      ))}

      <div className="mt-4 border-t border-line pt-4">
        {canAct ? (
          <Controls key={String(item.record.updated_at)} item={item} act={act} busy={action.isPending} />
        ) : (
          <p className="text-sm text-slate">
            <span className="font-semibold text-ink">View only.</span> {DEPT_LABEL[dept]} staff update this record; you can follow it here.
          </p>
        )}
        <div className="mt-3" aria-live="polite">
          <ErrorText error={action.error} />
          {done && (
            <p className="rounded-xl bg-tide-soft px-3 py-2 text-sm text-success">
              Saved: {done}. {item.taskStatus === "cleared" ? `${DEPT_LABEL[dept]} is now cleared for this patient.` : "CareFlow re-checked this department."}
            </p>
          )}
        </div>
      </div>

      {history.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs font-semibold text-slate">History ({history.length})</summary>
          <ul className="mt-2 flex flex-col gap-1 text-xs text-slate">
            {[...history].reverse().map((h, i) => (
              <li key={i}>
                <span className="tnum text-faint">{since(h.at)}</span> {h.summary} <span className="text-faint">by {h.by}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </Panel>
  );
}

export function Queue() {
  const { data: depts, isPending } = useMyDepartments();
  const params = useSearchParams();
  const router = useRouter();
  const dept = (params.get("dept") as Department | null) ?? depts?.[0]?.department;
  const canAct = depts?.find((d) => d.department === dept)?.canAct ?? false;
  const readOnly = Boolean(depts?.length) && depts!.every((d) => !d.canAct);
  const { data: items, isPending: loading, error } = useQueue(dept);
  const [showCleared, setShowCleared] = useState(false);
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const onTouched = (ref: string) => setTouched((t) => (t.has(ref) ? t : new Set(t).add(ref)));

  if (isPending) return <Skeleton className="h-40" />;
  const all = items ?? [];
  const summary = {
    open: all.filter((i) => i.taskStatus && i.taskStatus !== "cleared").length,
    blocked: all.filter((i) => i.taskStatus === "blocked" || i.blockers.length > 0).length,
    progress: all.filter((i) => i.taskStatus === "in_progress").length,
    waiting: all.filter((i) => i.taskStatus === "not_started").length,
    cleared: all.filter((i) => i.taskStatus === "cleared").length,
    escalated: all.filter((i) => Array.isArray(i.record.escalations) && (i.record.escalations as unknown[]).length > 0).length,
  };
  if (!depts?.length)
    return (
      <>
        <PageHeader title="My work queue" />
        <Panel className="p-6 text-sm text-slate">Your role doesn&apos;t own a department queue.</Panel>
      </>
    );

  const visible = (items ?? []).filter((i) => showCleared || i.taskStatus !== "cleared" || touched.has(i.patientRef));
  const hidden = (items?.length ?? 0) - visible.length;

  return (
    <>
      <PageHeader title={readOnly ? `${DEPT_LABEL[dept!]} queue` : `${DEPT_LABEL[dept!]} work queue`}>
        <p>
          Patients who need something from {DEPT_LABEL[dept!].toLowerCase()}. Blocked and overdue items come first.
          {canAct ? " Every update you make here is re-checked by CareFlow straight away." : " You have read-only access to this department."}
        </p>
      </PageHeader>

      {depts.length > 1 && (
        <Tabs<Department>
          wrap
          className="mb-4"
          label="Department"
          value={dept!}
          onChange={(d) => {
            setTouched(new Set());
            router.replace(`/queue?dept=${d}`, { scroll: false });
          }}
          options={depts.map((d) => ({ value: d.department, label: DEPT_LABEL[d.department] }))}
        />
      )}

      {!loading && !error && (
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <StatCard label="Open" value={summary.open} icon={ListTodo} tone="brand" help="Patients this department still has to clear." />
          <StatCard label="Blocked or overdue" value={summary.blocked} icon={AlertTriangle} tone="warning" help="A rule found a problem, such as a missed time limit." />
          <StatCard label="In progress" value={summary.progress} icon={Loader} tone="info" />
          <StatCard label="Not started" value={summary.waiting} icon={CircleDashed} />
          <StatCard label="Cleared" value={summary.cleared} icon={CheckCircle2} tone="success" />
          <StatCard label="Escalated" value={summary.escalated} icon={ArrowUpRight} tone="violet" help="Escalated to a duty lead after a coordinator approved it." />
        </div>
      )}

      {error ? (
        <Panel className="p-6 text-sm text-coral">{error.message}</Panel>
      ) : loading ? (
        <Skeleton className="h-48" />
      ) : visible.length === 0 ? (
        <Panel>
          <EmptyState icon={CheckCircle2} title={`Nothing waiting on ${DEPT_LABEL[dept!].toLowerCase()}`}>
            Patients appear here when they&apos;re admitted and need this department before discharge.
          </EmptyState>
        </Panel>
      ) : (
        <div className="flex flex-col gap-4">
          {visible.map((i) => (
            <Card key={i.patientRef} dept={dept!} item={i} onTouched={onTouched} canAct={canAct} />
          ))}
        </div>
      )}
      {hidden > 0 && (
        <button type="button" onClick={() => setShowCleared(true)} className="mt-4 text-sm font-semibold text-tide-deep hover:underline">
          Show {hidden} already cleared
        </button>
      )}
    </>
  );
}
