import clsx from "clsx";
import { Check, Hourglass, Lock, TriangleAlert } from "lucide-react";
import type { DepartmentTask, TaskStatus } from "@/lib/types";
import { DEPT_LABEL, TASK_LABEL, duration, minutesSince } from "@/lib/format";

const FILL: Record<TaskStatus, string> = {
  cleared: "bg-tide",
  in_progress: "bg-harbor/70",
  blocked: "bg-amber",
  not_started: "bg-transparent",
};

const FILL_WIDTH: Record<TaskStatus, string> = {
  cleared: "w-full",
  in_progress: "w-1/2",
  blocked: "w-full",
  not_started: "w-0",
};

/** Compact rail for list rows: one segment per department. */
export function MiniRail({ tasks }: { tasks: DepartmentTask[] }) {
  return (
    <div
      className="flex w-full gap-1"
      role="img"
      aria-label={tasks.map((t) => `${DEPT_LABEL[t.department]}: ${TASK_LABEL[t.status]}`).join(", ")}
    >
      {tasks.map((t) => (
        <div key={t.id} className="h-2 flex-1 overflow-hidden rounded-full bg-ink/[0.08]" title={`${DEPT_LABEL[t.department]}: ${TASK_LABEL[t.status]}`}>
          <div className={clsx("rail-fill h-full rounded-full", FILL[t.status], FILL_WIDTH[t.status])} />
        </div>
      ))}
    </div>
  );
}

const ICON: Record<TaskStatus, typeof Check> = {
  cleared: Check,
  in_progress: Hourglass,
  blocked: TriangleAlert,
  not_started: Lock,
};

/** The large rail in the patient workspace. Clinical authorization is shown as a recorded input, never as a step CareFlow decides. */
export function ClearanceRail({
  tasks,
  authorizedBy,
  authorizedAt,
}: {
  tasks: DepartmentTask[];
  authorizedBy: string;
  authorizedAt: string;
}) {
  const cleared = tasks.filter((t) => t.status === "cleared").length;
  const allClear = cleared === tasks.length;

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-lg font-bold text-ink">Administrative clearance</h2>
        <p className="tnum text-sm text-slate">
          {cleared} of {tasks.length} departments cleared, {duration(minutesSince(authorizedAt))} since authorization
        </p>
      </div>

      <ol
        className="grid gap-2 sm:[grid-template-columns:var(--rail-cols)]"
        style={{ ["--rail-cols" as string]: `minmax(0,0.9fr) repeat(${tasks.length}, minmax(0,1fr)) minmax(0,0.9fr)` }}
      >
        <li className="rounded-2xl border border-dashed border-ink/20 bg-surface-2 p-3">
          <p className="text-sm font-semibold text-ink">Clinically authorized</p>
          <p className="mt-1 text-xs leading-snug text-slate">Recorded by {authorizedBy}. CareFlow does not make this decision.</p>
        </li>

        {tasks.map((t) => {
          const Icon = ICON[t.status];
          const waited = duration(minutesSince(t.updatedAt));
          return (
            <li
              key={t.id}
              className={clsx(
                "relative overflow-hidden rounded-2xl border p-3",
                t.status === "cleared" && "border-tide/30 bg-tide-soft/70",
                t.status === "in_progress" && "border-harbor/25 bg-surface-2",
                t.status === "blocked" && "border-amber/60 bg-amber-soft/80",
                t.status === "not_started" && "border-line bg-surface-2",
              )}
            >
              <div className="flex items-center gap-2">
                <Icon
                  className={clsx(
                    "size-4 shrink-0",
                    t.status === "cleared" && "text-tide",
                    t.status === "in_progress" && "text-harbor",
                    t.status === "blocked" && "text-amber",
                    t.status === "not_started" && "text-faint",
                  )}
                  aria-hidden
                />
                <p className="text-sm font-semibold text-ink">{DEPT_LABEL[t.department]}</p>
              </div>
              <p className="tnum mt-1 text-xs text-slate">
                {TASK_LABEL[t.status]}
                {t.status !== "cleared" && t.status !== "not_started" && `, ${waited}`}
                {t.status === "not_started" && t.dependsOn.length > 0 && `, waits on ${t.dependsOn.map((d) => DEPT_LABEL[d].toLowerCase()).join(", ")}`}
              </p>
              <div className="absolute inset-x-3 bottom-0 h-1 overflow-hidden rounded-t-full bg-ink/[0.06]">
                <div className={clsx("rail-fill h-full", FILL[t.status], FILL_WIDTH[t.status])} />
              </div>
            </li>
          );
        })}

        <li
          className={clsx(
            "rounded-2xl border p-3",
            allClear ? "border-tide/40 bg-tide text-white" : "border-line bg-surface-2",
          )}
        >
          <p className={clsx("text-sm font-semibold", allClear ? "text-white" : "text-ink")}>Ready to discharge</p>
          <p className={clsx("mt-1 text-xs leading-snug", allClear ? "text-white/85" : "text-slate")}>
            {allClear ? "Every department verified against its own system." : "Unlocks when every department is verified."}
          </p>
        </li>
      </ol>
    </div>
  );
}
