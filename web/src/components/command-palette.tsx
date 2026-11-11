"use client";

import clsx from "clsx";
import { ArrowRight, ClipboardCheck, CornerDownLeft, LayoutList, Search, UserRound, type LucideIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listPatients } from "@/lib/api";
import { ACTION_SHORT, WORKFLOW_LABEL } from "@/lib/format";
import { useActions, useWorkflows } from "@/lib/queries";
import { useSession } from "@/lib/session";
import { Dialog } from "./ui";

export type NavItem = { href: string; label: string; icon: LucideIcon; perm: string; group: string; keywords?: string };

type Result = { key: string; group: string; label: string; detail?: string; href: string; icon: LucideIcon };

export function allowed(item: { perm: string }, perms: string[]) {
  return item.perm.endsWith(":") ? perms.some((p) => p.startsWith(item.perm)) : perms.includes(item.perm);
}

function useDebounced<T>(value: T, ms: number) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Ctrl/Cmd+K: jump to any page you can open, or find a patient, discharge workflow or approval. */
export function CommandPalette({ open, onClose, nav }: { open: boolean; onClose: () => void; nav: NavItem[] }) {
  const router = useRouter();
  const perms = useSession((s) => s.user?.permissions ?? []);
  const canRead = perms.includes("workflow:read");
  const [q, setQ] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const term = useDebounced(q.trim(), 200);

  const patients = useQuery({
    queryKey: ["palette-patients", term],
    queryFn: () => listPatients("all", term),
    enabled: open && canRead && term.length >= 2,
    staleTime: 15_000,
  });
  const workflows = useWorkflows();
  const actions = useActions();

  const results = useMemo<Result[]>(() => {
    const t = q.trim().toLowerCase();
    const match = (...s: (string | undefined)[]) => !t || s.some((x) => x?.toLowerCase().includes(t));
    const pages: Result[] = nav
      .filter((n) => allowed(n, perms) && match(n.label, n.keywords, n.group))
      .map((n) => ({ key: `p${n.href}`, group: "Go to", label: n.label, detail: n.group, href: n.href, icon: n.icon }));
    pages.push(
      ...[{ href: "/account", label: "Account and alerts", detail: "Settings" }]
        .filter((x) => match(x.label, "settings notifications telegram password install"))
        .map((x) => ({ key: `p${x.href}`, group: "Go to", ...x, icon: ArrowRight })),
    );
    if (!t) return pages;
    const pts: Result[] = (patients.data ?? []).slice(0, 6).map((p) => ({
      key: `pt${p.ref}`,
      group: "Patients",
      label: p.displayName,
      detail: `${p.mrn} · ${[p.ward, p.bed].filter(Boolean).join(", ")}`,
      href: p.workflow ? `/workflows/${p.workflow.id}` : `/patients?q=${encodeURIComponent(p.mrn)}`,
      icon: UserRound,
    }));
    const wfs: Result[] = canRead
      ? (workflows.data ?? [])
          .filter((w) => match(w.id, w.patient.mrn, w.patient.bed, w.patient.ward))
          .slice(0, 5)
          .map((w) => ({
            key: `w${w.id}`,
            group: "Discharges",
            label: w.id,
            detail: `${WORKFLOW_LABEL[w.status]} · ${[w.patient.ward, w.patient.bed].filter(Boolean).join(", ")}`,
            href: `/workflows/${w.id}`,
            icon: LayoutList,
          }))
      : [];
    const acts: Result[] = perms.includes("approval:decide")
      ? (actions.data ?? [])
          .filter((a) => match(a.id, a.workflowId, a.action.replace(/_/g, " ")))
          .slice(0, 5)
          .map((a) => ({
            key: `a${a.id}`,
            group: "Approvals",
            label: `${a.id} · ${a.action.replace(/_/g, " ")}`,
            detail: `${ACTION_SHORT[a.status]} · ${a.workflowId}`,
            href: `/approvals?id=${a.id}`,
            icon: ClipboardCheck,
          }))
      : [];
    return [...pages, ...pts, ...wfs, ...acts];
  }, [q, nav, perms, patients.data, workflows.data, actions.data, canRead]);

  useEffect(() => {
    if (open) setTimeout(() => input.current?.focus(), 0);
  }, [open]);

  const go = (r: Result | undefined) => {
    if (!r) return;
    onClose();
    router.push(r.href);
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setIndex((i) => Math.min(results.length - 1, i + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setIndex((i) => Math.max(0, i - 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(results[index]);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="Search CareFlow" size="lg">
      <div className="-mt-1">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
          <input
            ref={input}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setIndex(0);
            }}
            onKeyDown={onKey}
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-results"
            aria-activedescendant={results[index] ? `pal-${results[index].key}` : undefined}
            placeholder={canRead ? "Pages, patients (name or MRN), workflow or approval IDs…" : "Pages…"}
            className="h-11 w-full rounded-[10px] border border-line-strong bg-surface pl-9 pr-3 text-[15px] text-ink placeholder:text-faint focus:border-tide focus:outline-none focus:ring-3 focus:ring-tide/15"
          />
        </div>
        <ul id="palette-results" role="listbox" aria-label="Results" className="scroll-quiet mt-3 max-h-[52vh] overflow-y-auto pb-1">
          {results.map((r, i) => {
            const header = i === 0 || results[i - 1].group !== r.group ? r.group : null;
            const Icon = r.icon;
            return (
              <li key={r.key} role="presentation">
                {header && <p className="px-2 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint first:pt-0">{header}</p>}
                <div
                  id={`pal-${r.key}`}
                  role="option"
                  aria-selected={i === index}
                  onMouseEnter={() => setIndex(i)}
                  onClick={() => go(r)}
                  className={clsx("flex cursor-pointer items-center gap-3 rounded-[10px] px-2.5 py-2", i === index ? "bg-hover" : "")}
                >
                  <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-surface-2 text-slate ring-1 ring-line">
                    <Icon className="size-3.5" aria-hidden />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium text-ink">{r.label}</span>
                    {r.detail && <span className="block truncate text-xs text-faint">{r.detail}</span>}
                  </span>
                  {i === index && <CornerDownLeft className="size-3.5 text-faint" aria-hidden />}
                </div>
              </li>
            );
          })}
          {results.length === 0 && (
            <li className="px-2 py-8 text-center text-sm text-slate">
              {patients.isFetching ? "Searching…" : `Nothing matches “${q}”.`}
            </li>
          )}
        </ul>
        <p className="mt-2 hidden border-t border-line pt-3 text-xs text-faint sm:block">
          <Kbd>↑</Kbd> <Kbd>↓</Kbd> to move · <Kbd>Enter</Kbd> to open · <Kbd>Esc</Kbd> to close
        </p>
      </div>
    </Dialog>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex min-w-5 items-center justify-center rounded-[5px] border border-line-strong bg-surface px-1 font-sans text-[11px] font-semibold text-slate shadow-[0_1px_0_var(--color-line-strong)]">
      {children}
    </kbd>
  );
}

export const SHORTCUTS: [string[], string][] = [
  [["Ctrl", "K"], "Search pages, patients, discharges and approvals"],
  [["/"], "Open search"],
  [["["], "Collapse or expand the sidebar"],
  [["?"], "Show these shortcuts"],
  [["Esc"], "Close a dialog or panel"],
];

export function ShortcutsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Dialog open={open} onClose={onClose} title="Keyboard shortcuts" description="On a Mac, use ⌘ instead of Ctrl.">
      <ul className="divide-y divide-line">
        {SHORTCUTS.map(([keys, what]) => (
          <li key={what} className="flex items-center justify-between gap-4 py-2.5 text-sm text-ink">
            {what}
            <span className="flex shrink-0 gap-1">
              {keys.map((k) => (
                <Kbd key={k}>{k}</Kbd>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}
