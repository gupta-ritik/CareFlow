"use client";

import clsx from "clsx";
import { Bell, ClipboardCheck } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { since } from "@/lib/format";
import { useActions } from "@/lib/queries";
import { useSession } from "@/lib/session";

const SEEN_KEY = "careflow-notifications-seen";
const APPROVE_ANY = ["Administrator", "Hospital director"];

function readSeen() {
  try {
    return Number(localStorage.getItem(SEEN_KEY) || 0);
  } catch {
    return 0;
  }
}

/** Approvals waiting for the signed-in person. Shows the action, workflow and time, never patient names. */
export function NotificationCenter() {
  const user = useSession((s) => s.user);
  const canDecide = Boolean(user?.permissions.includes("approval:decide"));
  const { data } = useActions();
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState(() => (typeof window === "undefined" ? 0 : readSeen()));
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const mine = canDecide
    ? (data ?? [])
        .filter((a) => a.status === "pending" && (APPROVE_ANY.includes(user?.role ?? "") || a.approverRole === user?.role))
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt))
    : [];
  const unread = mine.filter((a) => new Date(a.createdAt).getTime() > seen).length;

  const toggle = () => {
    setOpen((o) => {
      if (!o) {
        const now = Date.now();
        try {
          localStorage.setItem(SEEN_KEY, String(now));
        } catch {}
        setTimeout(() => setSeen(now), 1500);
      }
      return !o;
    });
  };

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-haspopup="true"
        aria-label={unread ? `Notifications, ${unread} new` : "Notifications"}
        title="Notifications"
        className="relative grid size-9 place-items-center rounded-[10px] text-slate hover:bg-hover hover:text-ink"
      >
        <Bell className="size-[18px]" aria-hidden />
        {unread > 0 && (
          <span className="tnum absolute right-1 top-1 grid min-w-4 place-items-center rounded-full bg-coral px-1 text-[10px] font-bold leading-4 text-white">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
      {open && (
        <div className="overlay pop-in absolute right-0 z-40 mt-2 w-[min(22rem,calc(100vw-1.5rem))] rounded-2xl">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <p className="text-sm font-semibold text-ink">Notifications</p>
            {mine.length > 0 && <span className="text-xs text-faint">{mine.length} waiting for you</span>}
          </div>
          {mine.length === 0 ? (
            <div className="px-4 py-8 text-center">
              <p className="text-sm font-medium text-ink">You&apos;re all caught up</p>
              <p className="mt-1 text-xs text-slate">
                {canDecide ? "Approvals for your role appear here as soon as CareFlow proposes them." : "Your role doesn't decide approvals."}
              </p>
            </div>
          ) : (
            <ul className="scroll-quiet max-h-80 divide-y divide-line overflow-y-auto">
              {mine.slice(0, 20).map((a) => {
                const fresh = new Date(a.createdAt).getTime() > seen;
                return (
                  <li key={a.id}>
                    <Link
                      href={`/approvals?id=${a.id}`}
                      onClick={() => setOpen(false)}
                      className="flex gap-3 px-4 py-3 hover:bg-hover"
                    >
                      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-violet-soft text-violet">
                        <ClipboardCheck className="size-4" aria-hidden />
                      </span>
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-medium text-ink first-letter:uppercase">{a.action.replace(/_/g, " ")}</span>
                        <span className="block text-xs text-slate">
                          Approval needed · {a.workflowId} · {since(a.createdAt)}
                        </span>
                      </span>
                      <span className={clsx("mt-1.5 size-2 shrink-0 rounded-full", fresh ? "bg-tide" : "bg-transparent")} aria-label={fresh ? "New" : undefined} />
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
          <div className="border-t border-line px-4 py-2.5 text-right">
            <Link href={canDecide ? "/approvals" : "/account"} onClick={() => setOpen(false)} className="text-xs font-semibold text-tide-deep hover:underline">
              {canDecide ? "Open approvals" : "Alert settings"}
            </Link>
          </div>
        </div>
      )}
    </div>
  );
}
