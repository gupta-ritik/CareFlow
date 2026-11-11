import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import type { RiskLevel, TaskStatus, WorkflowStatus } from "@/lib/types";
import { RISK_LABEL, TASK_LABEL, WORKFLOW_LABEL } from "@/lib/format";

export function Panel({
  children,
  className,
  strong,
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  strong?: boolean;
  as?: "section" | "div" | "aside" | "article";
}) {
  return <Tag className={clsx(strong ? "glass-strong" : "glass", "rounded-[20px]", className)}>{children}</Tag>;
}

const WF_TONE: Record<WorkflowStatus, string> = {
  in_progress: "bg-harbor-soft text-harbor",
  blocked: "bg-amber-soft text-[#8a560c]",
  awaiting_approval: "bg-[#e6e1f6] text-[#4b3d93]",
  cleared: "bg-tide-soft text-[#0a6b64]",
  discharged: "bg-ink/[0.06] text-slate",
};

export function WorkflowPill({ status }: { status: WorkflowStatus }) {
  return (
    <span className={clsx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold", WF_TONE[status])}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {WORKFLOW_LABEL[status]}
    </span>
  );
}

const TASK_TONE: Record<TaskStatus, string> = {
  cleared: "text-[#0a6b64]",
  in_progress: "text-harbor",
  blocked: "text-[#8a560c]",
  not_started: "text-faint",
};

export function TaskStatusText({ status }: { status: TaskStatus }) {
  return <span className={clsx("text-sm font-semibold", TASK_TONE[status])}>{TASK_LABEL[status]}</span>;
}

const RISK_TONE: Record<RiskLevel, string> = {
  low: "border-tide/40 text-[#0a6b64]",
  medium: "border-amber/50 text-[#8a560c]",
  high: "border-coral/50 text-coral",
};

export function RiskTag({ risk }: { risk: RiskLevel }) {
  return (
    <span className={clsx("inline-flex rounded-full border bg-white/50 px-2.5 py-0.5 text-xs font-semibold", RISK_TONE[risk])}>
      {RISK_LABEL[risk]}
    </span>
  );
}

export function EvidenceRef({ id }: { id: string }) {
  return (
    <span className="tnum inline-flex w-fit self-start rounded-md bg-ink/[0.06] px-1.5 py-0.5 text-xs font-semibold text-slate">{id}</span>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-xl bg-white/50", className)} />;
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-col gap-2">
      <h1 className="text-[1.75rem] font-bold leading-tight tracking-[-0.02em] text-ink sm:text-[2rem]">{title}</h1>
      {children && <div className="max-w-[68ch] text-[0.95rem] leading-relaxed text-slate">{children}</div>}
    </header>
  );
}

export const inputClass =
  "w-full rounded-xl border border-white/80 bg-white/75 px-3 py-2.5 text-sm text-ink placeholder:text-faint disabled:opacity-60";

export function Field({ label, htmlFor, hint, children }: { label: string; htmlFor: string; hint?: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <label htmlFor={htmlFor} className="text-sm font-semibold text-ink">
        {label}
      </label>
      <div className="mt-1.5">{children}</div>
      {hint && <p className="mt-1 text-xs text-faint">{hint}</p>}
    </div>
  );
}

export function Button({
  children,
  variant = "primary",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "quiet" | "good" | "danger" }) {
  const styles = {
    primary: "bg-ink text-white hover:bg-ink/90",
    secondary: "border border-white/80 bg-white/65 text-ink hover:bg-white",
    quiet: "text-slate hover:bg-white/60 hover:text-ink",
    good: "bg-tide text-white hover:bg-[#0b7b73]",
    danger: "text-coral hover:bg-coral-soft",
  }[variant];
  return (
    <button
      type="button"
      {...props}
      className={clsx("inline-flex items-center justify-center gap-2 rounded-xl px-3.5 py-2 text-sm font-semibold disabled:opacity-50", styles, className)}
    >
      {children}
    </button>
  );
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="rounded-xl bg-coral-soft/80 px-3 py-2 text-sm text-coral">
      {(error as Error).message}
    </p>
  );
}
