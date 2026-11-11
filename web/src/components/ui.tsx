"use client";

import clsx from "clsx";
import { Info, X, type LucideIcon } from "lucide-react";
import { useEffect, useId, useRef, type ButtonHTMLAttributes, type ReactNode } from "react";
import { create } from "zustand";
import type { RiskLevel, TaskStatus, WorkflowStatus } from "@/lib/types";
import { RISK_LABEL, TASK_LABEL, WORKFLOW_LABEL } from "@/lib/format";

/* ---------------------------------------------------------------------------------------------------------------
   CareFlow UI kit. Every screen builds from these, so spacing, radii, colour and states stay consistent.
   Radii: cards 14px, controls 10px, dialogs 16px. Control height: 36px (sm 30px). Spacing on a 4px grid.
   --------------------------------------------------------------------------------------------------------------- */

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
  return <Tag className={clsx(strong ? "card-raised" : "card", "rounded-[14px]", className)}>{children}</Tag>;
}

/** A titled card section: heading row (with optional actions) above its content. */
export function Section({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <Panel className={className}>
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line px-5 py-4">
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
          {description && <p className="mt-0.5 text-[13px] text-slate">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      <div className={clsx("p-5", bodyClassName)}>{children}</div>
    </Panel>
  );
}

/* ---- status ------------------------------------------------------------------------------------------------- */
export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "violet" | "brand";

const TONE: Record<Tone, string> = {
  neutral: "bg-surface-2 text-slate ring-line",
  success: "bg-success-soft text-success ring-success/15",
  warning: "bg-amber-soft text-amber-ink ring-amber/20",
  danger: "bg-coral-soft text-coral-ink ring-coral/20",
  info: "bg-harbor-soft text-harbor ring-harbor/15",
  violet: "bg-violet-soft text-violet ring-violet/15",
  brand: "bg-tide-soft text-tide-deep ring-tide/20",
};

/** Compact status badge. Always carries text: colour is never the only signal. */
export function Badge({ tone = "neutral", dot = true, children, className }: { tone?: Tone; dot?: boolean; children: ReactNode; className?: string }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-semibold ring-1 ring-inset",
        TONE[tone],
        className,
      )}
    >
      {dot && <span className="size-1.5 rounded-full bg-current" aria-hidden />}
      {children}
    </span>
  );
}

export const WORKFLOW_TONE: Record<WorkflowStatus, Tone> = {
  in_progress: "info",
  blocked: "warning",
  awaiting_approval: "violet",
  cleared: "success",
  discharged: "neutral",
};

export function WorkflowPill({ status }: { status: WorkflowStatus }) {
  return <Badge tone={WORKFLOW_TONE[status]}>{WORKFLOW_LABEL[status]}</Badge>;
}

const TASK_TONE: Record<TaskStatus, string> = {
  cleared: "text-success",
  in_progress: "text-harbor",
  blocked: "text-amber-ink",
  not_started: "text-faint",
};

export function TaskStatusText({ status }: { status: TaskStatus }) {
  return <span className={clsx("text-sm font-semibold", TASK_TONE[status])}>{TASK_LABEL[status]}</span>;
}

const RISK_TONE: Record<RiskLevel, Tone> = { low: "success", medium: "warning", high: "danger" };

export function RiskTag({ risk }: { risk: RiskLevel }) {
  return (
    <Badge tone={RISK_TONE[risk]} dot={false}>
      {RISK_LABEL[risk]}
    </Badge>
  );
}

export function EvidenceRef({ id }: { id: string }) {
  return (
    <span className="tnum inline-flex w-fit self-start rounded-md border border-line bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] font-semibold text-slate">
      {id}
    </span>
  );
}

/* ---- layout ------------------------------------------------------------------------------------------------- */
export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("animate-pulse rounded-[10px] bg-line/70", className)} aria-hidden />;
}

/** Page heading: title, supporting description and the page's primary actions on the right. */
export function PageHeader({ title, children, actions, eyebrow }: { title: string; children?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <header className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        {eyebrow && <p className="mb-1 text-xs font-semibold uppercase tracking-[0.08em] text-tide-deep">{eyebrow}</p>}
        <h1 className="text-[26px] font-bold leading-tight tracking-[-0.02em] text-ink sm:text-[28px]">{title}</h1>
        {children && <div className="mt-1.5 max-w-[72ch] text-[14px] leading-relaxed text-slate">{children}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** A KPI tile: label, value, optional context line and an explanation on hover/focus. */
export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "neutral",
  help,
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: LucideIcon;
  tone?: Tone;
  help?: string;
  className?: string;
}) {
  const iconTone: Record<Tone, string> = {
    neutral: "bg-surface-2 text-slate",
    success: "bg-success-soft text-success",
    warning: "bg-amber-soft text-amber-ink",
    danger: "bg-coral-soft text-coral-ink",
    info: "bg-harbor-soft text-harbor",
    violet: "bg-violet-soft text-violet",
    brand: "bg-tide-soft text-tide-deep",
  };
  return (
    <Panel className={clsx("flex flex-col gap-3 p-4", className)}>
      <div className="flex items-center justify-between gap-2">
        <p className="flex items-center gap-1.5 text-[13px] font-medium text-slate">
          {label}
          {help && <HelpTip text={help} />}
        </p>
        {Icon && (
          <span className={clsx("grid size-8 place-items-center rounded-[9px]", iconTone[tone])}>
            <Icon className="size-4" aria-hidden />
          </span>
        )}
      </div>
      <p className="tnum text-[26px] font-bold leading-none tracking-[-0.02em] text-ink">{value}</p>
      {hint && <p className="text-xs text-faint">{hint}</p>}
    </Panel>
  );
}

/** A small "i" that explains a metric. Keyboard focusable; the text is also its accessible name. */
export function HelpTip({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex">
      <button type="button" aria-label={text} className="rounded-full text-faint hover:text-slate focus-visible:text-slate">
        <Info className="size-3.5" aria-hidden />
      </button>
      <span
        role="tooltip"
        className="overlay pointer-events-none absolute left-1/2 top-full z-30 mt-1.5 hidden w-56 -translate-x-1/2 rounded-[10px] px-3 py-2 text-xs font-normal leading-relaxed text-slate group-focus-within:block group-hover:block"
      >
        {text}
      </span>
    </span>
  );
}

/** What happened and what to do next, when a list is empty. */
export function EmptyState({ icon: Icon, title, children, action }: { icon?: LucideIcon; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center px-6 py-12 text-center">
      {Icon && (
        <span className="grid size-11 place-items-center rounded-xl bg-tide-soft text-tide-deep">
          <Icon className="size-5" aria-hidden />
        </span>
      )}
      <p className="mt-3 text-[15px] font-semibold text-ink">{title}</p>
      {children && <div className="mt-1 max-w-[46ch] text-[13px] leading-relaxed text-slate">{children}</div>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** Segmented tabs / filters. Arrow keys move between options. */
export function Tabs<T extends string>({
  value,
  onChange,
  options,
  label,
  className,
  wrap,
}: {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; count?: number }[];
  label: string;
  className?: string;
  wrap?: boolean;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = (i + (e.key === "ArrowRight" ? 1 : -1) + options.length) % options.length;
    refs.current[next]?.focus();
    onChange(options[next].value);
  };
  return (
    <div role="tablist" aria-label={label} className={clsx("inline-flex max-w-full gap-1 rounded-[10px] bg-surface-2 p-1 ring-1 ring-inset ring-line", wrap ? "flex-wrap" : "overflow-x-auto", className)}>
      {options.map((o, i) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onKeyDown={(e) => onKey(e, i)}
            onClick={() => onChange(o.value)}
            className={clsx(
              "inline-flex items-center gap-1.5 whitespace-nowrap rounded-[7px] px-3 py-1.5 text-[13px] font-semibold transition-colors",
              active ? "bg-surface text-ink shadow-[var(--shadow-card)] ring-1 ring-line" : "text-slate hover:text-ink",
            )}
          >
            {o.label}
            {o.count !== undefined && (
              <span className={clsx("tnum rounded-full px-1.5 text-[11px]", active ? "bg-tide-soft text-tide-deep" : "bg-line/80 text-slate")}>{o.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/* ---- forms -------------------------------------------------------------------------------------------------- */
export const inputClass =
  "h-9 w-full rounded-[10px] border border-line-strong bg-surface px-3 text-sm text-ink shadow-[0_1px_1px_rgba(16,36,58,0.03)] placeholder:text-faint transition-colors hover:border-[#b9c6d3] focus:border-tide focus:outline-none focus:ring-3 focus:ring-tide/15 disabled:bg-surface-2 disabled:text-disabled aria-invalid:border-coral";

export const selectClass = clsx(inputClass, "select-chevron");

export function Field({
  label,
  htmlFor,
  hint,
  error,
  required,
  children,
}: {
  label: string;
  htmlFor: string;
  hint?: string;
  error?: string | null;
  required?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={htmlFor} className="text-[13px] font-semibold text-ink">
        {label}
        {required && (
          <span className="ml-0.5 text-coral" aria-hidden>
            *
          </span>
        )}
      </label>
      <div className="mt-1.5">{children}</div>
      {error ? (
        <p id={`${htmlFor}-error`} role="alert" className="mt-1 text-xs font-medium text-coral-ink">
          {error}
        </p>
      ) : (
        hint && <p className="mt-1 text-xs text-faint">{hint}</p>
      )}
    </div>
  );
}

type Variant = "primary" | "secondary" | "quiet" | "good" | "danger" | "dangerSolid";

export function Button({
  children,
  variant = "primary",
  size = "md",
  loading,
  className,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }) {
  const styles: Record<Variant, string> = {
    primary: "bg-navy text-white shadow-[0_1px_2px_rgba(16,36,58,0.2)] hover:bg-navy-deep",
    secondary: "border border-line-strong bg-surface text-ink shadow-[0_1px_1px_rgba(16,36,58,0.04)] hover:bg-surface-2",
    quiet: "text-slate hover:bg-hover hover:text-ink",
    good: "bg-tide-deep text-white shadow-[0_1px_2px_rgba(8,127,117,0.25)] hover:bg-[#066d64]",
    danger: "text-coral-ink hover:bg-coral-soft",
    dangerSolid: "bg-coral text-white hover:bg-coral-ink",
  };
  return (
    <button
      type="button"
      {...props}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        "inline-flex items-center justify-center gap-2 rounded-[10px] font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-[30px] px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        styles[variant],
        className,
      )}
    >
      {loading && <span className="size-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" aria-hidden />}
      {children}
    </button>
  );
}

export function ErrorText({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <p role="alert" className="rounded-[10px] border border-coral/20 bg-coral-soft px-3 py-2 text-sm text-coral-ink">
      {(error as Error).message}
    </p>
  );
}

/* ---- overlays ----------------------------------------------------------------------------------------------- */
function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
}

/** Keep keyboard focus inside an open dialog or drawer, and return it to where it was on close. */
function useFocusTrap(open: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open || !ref.current) return;
    const previous = document.activeElement as HTMLElement | null;
    const root = ref.current;
    const focusables = () =>
      Array.from(root.querySelectorAll<HTMLElement>('a[href],button:not([disabled]),input:not([disabled]),select,textarea,[tabindex]:not([tabindex="-1"])'));
    (focusables()[0] ?? root).focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Tab") return;
      const f = focusables();
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) {
        e.preventDefault();
        f[f.length - 1].focus();
      } else if (!e.shiftKey && document.activeElement === f[f.length - 1]) {
        e.preventDefault();
        f[0].focus();
      }
    };
    root.addEventListener("keydown", onKey);
    return () => {
      root.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open]);
  return ref;
}

export function Dialog({
  open,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "md" | "lg";
}) {
  const id = useId();
  useEscape(open, onClose);
  const ref = useFocusTrap(open);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4">
      <div className="fade-in absolute inset-0 bg-navy/30" onClick={onClose} aria-hidden />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-t`}
        tabIndex={-1}
        className={clsx(
          "overlay pop-in relative flex max-h-[92vh] w-full flex-col rounded-t-2xl sm:rounded-2xl",
          size === "lg" ? "sm:max-w-2xl" : "sm:max-w-md",
        )}
      >
        <div className="flex items-start justify-between gap-4 px-5 pt-5">
          <div>
            <h2 id={`${id}-t`} className="text-[17px] font-semibold text-ink">
              {title}
            </h2>
            {description && <div className="mt-1 text-[13px] leading-relaxed text-slate">{description}</div>}
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="-mr-1 rounded-lg p-1 text-faint hover:bg-hover hover:text-ink">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        {children && <div className="scroll-quiet overflow-y-auto px-5 pt-4">{children}</div>}
        {footer && <div className="mt-5 flex flex-wrap justify-end gap-2 border-t border-line px-5 py-4">{footer}</div>}
        {!footer && <div className="pb-5" />}
      </div>
    </div>
  );
}

/** A right-hand panel for details; full screen on phones. */
export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const id = useId();
  useEscape(open, onClose);
  const ref = useFocusTrap(open);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50">
      <div className="fade-in absolute inset-0 bg-navy/25" onClick={onClose} aria-hidden />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-t`}
        tabIndex={-1}
        className="overlay slide-in absolute inset-y-0 right-0 flex w-full flex-col sm:max-w-xl sm:rounded-l-2xl"
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <h2 id={`${id}-t`} className="truncate text-[17px] font-semibold text-ink">
              {title}
            </h2>
            {subtitle && <div className="mt-0.5 text-[13px] text-slate">{subtitle}</div>}
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="-mr-1 rounded-lg p-1.5 text-faint hover:bg-hover hover:text-ink">
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="scroll-quiet flex-1 overflow-y-auto px-5 py-5">{children}</div>
        {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-line px-5 py-4">{footer}</div>}
      </div>
    </div>
  );
}

/** Asks before something consequential; the confirm button shows progress and the error stays in the dialog. */
export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  children,
  confirmLabel,
  tone = "primary",
  busy,
  error,
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  tone?: "primary" | "good" | "dangerSolid";
  busy?: boolean;
  error?: unknown;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      description={children}
      footer={
        <>
          <Button variant="secondary" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant={tone} onClick={onConfirm} loading={busy}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      {error ? <ErrorText error={error} /> : null}
    </Dialog>
  );
}

/* ---- toasts ------------------------------------------------------------------------------------------------- */
type Toast = { id: number; tone: "success" | "error" | "info"; message: string };
const useToasts = create<{ items: Toast[]; push: (t: Omit<Toast, "id">) => void; drop: (id: number) => void }>((set) => ({
  items: [],
  push: (t) => {
    const id = Date.now() + Math.random();
    set((s) => ({ items: [...s.items.slice(-3), { ...t, id }] }));
    setTimeout(() => set((s) => ({ items: s.items.filter((x) => x.id !== id) })), t.tone === "error" ? 7000 : 4000);
  },
  drop: (id) => set((s) => ({ items: s.items.filter((x) => x.id !== id) })),
}));

/** Call only after the server confirmed the operation; never optimistically. */
export const toast = {
  success: (message: string) => useToasts.getState().push({ tone: "success", message }),
  error: (message: string) => useToasts.getState().push({ tone: "error", message }),
  info: (message: string) => useToasts.getState().push({ tone: "info", message }),
};

export function Toaster() {
  const { items, drop } = useToasts();
  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-3 bottom-3 z-[60] flex flex-col items-end gap-2 sm:inset-x-auto sm:right-5 sm:bottom-5">
      {items.map((t) => (
        <div
          key={t.id}
          role={t.tone === "error" ? "alert" : "status"}
          className="overlay pop-in pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-xl px-4 py-3 text-sm text-ink"
        >
          <span
            className={clsx("mt-1.5 size-2 shrink-0 rounded-full", t.tone === "success" ? "bg-success" : t.tone === "error" ? "bg-coral" : "bg-harbor")}
            aria-hidden
          />
          <p className="flex-1">{t.message}</p>
          <button type="button" onClick={() => drop(t.id)} aria-label="Dismiss" className="rounded p-0.5 text-faint hover:text-ink">
            <X className="size-3.5" aria-hidden />
          </button>
        </div>
      ))}
    </div>
  );
}
