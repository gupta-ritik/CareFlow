"use client";

import clsx from "clsx";
import { BarChart3, BedDouble, ClipboardCheck, ClipboardList, Gauge, History, KeyRound, LayoutList, LogOut, Menu, MessageSquareText, Plug, UserRound, Users, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { demoInfo, demoLogin } from "@/lib/api";
import { useActions } from "@/lib/queries";
import { useHydrated, useSession } from "@/lib/session";
import type { DemoRole } from "@/lib/types";

const NAV = [
  { href: "/director", label: "Director dashboard", icon: Gauge, perm: "executive:read" },
  { href: "/ask", label: "Ask CareFlow", icon: MessageSquareText, perm: "ask:use" },
  { href: "/", label: "Discharge board", icon: LayoutList, perm: "workflow:read" },
  { href: "/patients", label: "Patients", icon: UserRound, perm: "workflow:read" },
  { href: "/queue", label: "Department queues", icon: ClipboardList, perm: "dept:" },
  { href: "/beds", label: "Beds", icon: BedDouble, perm: "workflow:read" },
  { href: "/approvals", label: "Approvals", icon: ClipboardCheck, perm: "approval:decide" },
  { href: "/operations", label: "Operations", icon: BarChart3, perm: "analytics:read" },
  { href: "/audit", label: "Audit trail", icon: History, perm: "audit:read" },
  { href: "/integrations", label: "Integrations", icon: Plug, perm: "integration:read" },
  { href: "/users", label: "Users", icon: Users, perm: "user:manage" },
];

const PUBLIC = ["/login", "/setup"];

export function Mark() {
  return (
    <svg viewBox="0 0 32 32" className="size-8" aria-hidden>
      <rect width="32" height="32" rx="10" fill="#0f2236" />
      <path d="M9 19a7 7 0 0 1 12.5-4.3" stroke="#5fd3c8" strokeWidth="3" fill="none" strokeLinecap="round" />
      <path d="M23 13a7 7 0 0 1-12.5 4.3" stroke="#8fb3f2" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const perms = useSession((s) => s.user?.permissions ?? []);
  const canDecide = perms.includes("approval:decide");
  const { data: actions } = useActions();
  const pending = canDecide ? (actions?.filter((a) => a.status === "pending").length ?? 0) : 0;
  const items = NAV.filter((n) => (n.perm.endsWith(":") ? perms.some((p) => p.startsWith(n.perm)) : perms.includes(n.perm)));

  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {items.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" || pathname.startsWith("/workflows") : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "flex items-center gap-3 rounded-xl px-3 py-2.5 text-[0.94rem] font-semibold transition-colors",
              active ? "bg-white/80 text-ink shadow-[0_1px_0_rgba(255,255,255,.9)_inset,0_6px_16px_-8px_rgba(22,60,98,.25)]" : "text-slate hover:bg-white/45 hover:text-ink",
            )}
          >
            <Icon className={clsx("size-[18px]", active ? "text-harbor" : "text-faint")} aria-hidden />
            <span className="flex-1">{label}</span>
            {href === "/approvals" && pending > 0 && (
              <span className="tnum rounded-full bg-[#4b3d93] px-2 py-0.5 text-xs font-bold text-white" aria-label={`${pending} pending`}>
                {pending}
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );
}

/** Shown on every page while signed in through "Try the demo": says the data is synthetic and switches role. */
function DemoBar() {
  const user = useSession((s) => s.user);
  const signIn = useSession((s) => s.signIn);
  const qc = useQueryClient();
  const router = useRouter();
  const [roles, setRoles] = useState<DemoRole[]>([]);
  const [busy, setBusy] = useState(false);
  const isDemo = Boolean(user?.demo);

  useEffect(() => {
    if (!isDemo) return;
    demoInfo()
      .then((d) => setRoles(d.enabled ? d.roles : []))
      .catch(() => setRoles([]));
  }, [isDemo]);

  if (!isDemo || !user) return null;

  const switchTo = async (key: string) => {
    setBusy(true);
    try {
      const r = await demoLogin(key);
      qc.clear();
      signIn(r.accessToken, r.user);
      router.replace(r.home);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="status" className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-2xl bg-tide-soft/80 px-4 py-2.5 text-sm text-ink">
      <span className="font-semibold">Demo hospital</span>
      <span className="text-xs text-slate">Synthetic patients only. Try anything; the data resets daily.</span>
      {roles.length > 0 && (
        <label className="ml-auto flex items-center gap-2 text-xs font-semibold text-slate">
          View as
          <select
            value={user.demoRole ?? ""}
            disabled={busy}
            onChange={(e) => switchTo(e.target.value)}
            className="rounded-lg border border-white/80 bg-white/90 px-2 py-1 text-xs font-semibold text-ink disabled:opacity-60"
          >
            {roles.map((r) => (
              <option key={r.key} value={r.key}>
                {r.role}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );
}

function SessionCard({ onNavigate }: { onNavigate?: () => void }) {
  const { user, signOut } = useSession();
  const qc = useQueryClient();
  const router = useRouter();
  if (!user) return null;
  return (
    <div className="rounded-2xl bg-white/40 p-3">
      <p className="truncate text-sm font-semibold text-ink">{user.name}</p>
      <p className="truncate text-xs text-slate">{user.role}</p>
      <div className="mt-2 flex gap-1">
        {!user.demo && (
          <Link href="/account" onClick={onNavigate} className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-semibold text-slate hover:bg-white/60 hover:text-ink">
            <KeyRound className="size-3.5" aria-hidden /> Password
          </Link>
        )}
        <button
          type="button"
          onClick={() => {
            signOut();
            qc.clear();
            router.replace("/login");
          }}
          className="inline-flex items-center gap-1.5 rounded-lg px-2 py-1.5 text-xs font-semibold text-slate hover:bg-white/60 hover:text-ink"
        >
          <LogOut className="size-3.5" aria-hidden /> Sign out
        </button>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const token = useSession((s) => s.token);
  const hydrated = useHydrated();
  const isPublic = PUBLIC.includes(pathname);
  const isPrint = pathname.endsWith("/report");

  useEffect(() => {
    if (hydrated && !token && !isPublic) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [hydrated, token, isPublic, pathname, router]);

  if (isPublic) return <>{children}</>;
  if (!hydrated || !token) return <div className="min-h-screen" aria-busy="true" />;
  if (isPrint) return <main id="main">{children}</main>;

  return (
    <div className="mx-auto flex min-h-screen max-w-[1440px] gap-5 p-3 sm:p-5">
      <aside className="glass sticky top-5 hidden h-[calc(100vh-2.5rem)] w-64 shrink-0 flex-col justify-between rounded-[28px] p-4 lg:flex">
        <div className="flex flex-col gap-8">
          <Link href="/" className="flex items-center gap-2.5 px-1">
            <Mark />
            <span className="text-lg font-bold tracking-[-0.01em] text-ink">CareFlow</span>
          </Link>
          <NavLinks />
        </div>
        <SessionCard />
      </aside>

      <div className="glass fixed inset-x-3 top-3 z-30 flex items-center justify-between rounded-2xl px-3 py-2 lg:hidden">
        <Link href="/" className="flex items-center gap-2">
          <Mark />
          <span className="font-bold text-ink">CareFlow</span>
        </Link>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          aria-controls="mobile-nav"
          aria-label={open ? "Close menu" : "Open menu"}
          className="rounded-xl p-2 text-ink hover:bg-white/50"
        >
          {open ? <X className="size-5" /> : <Menu className="size-5" />}
        </button>
      </div>
      {open && (
        <div id="mobile-nav" className="glass-strong fixed inset-x-3 top-[4.25rem] z-30 flex flex-col gap-4 rounded-2xl p-3 lg:hidden">
          <NavLinks onNavigate={() => setOpen(false)} />
          <SessionCard onNavigate={() => setOpen(false)} />
        </div>
      )}

      <main id="main" className="min-w-0 flex-1 pt-16 pb-10 lg:pt-2">
        <DemoBar />
        {children}
      </main>
    </div>
  );
}
