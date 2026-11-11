"use client";

import clsx from "clsx";
import {
  BarChart3,
  BedDouble,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ClipboardCheck,
  ClipboardList,
  CircleHelp,
  Gauge,
  History,
  Keyboard,
  LayoutList,
  LogOut,
  Menu,
  MessageSquareText,
  Plug,
  Search,
  Settings,
  UserRound,
  Users,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { demoInfo, demoLogin } from "@/lib/api";
import { useActions } from "@/lib/queries";
import { useHydrated, useSession } from "@/lib/session";
import type { DemoRole } from "@/lib/types";
import { CommandPalette, Kbd, ShortcutsDialog, allowed, type NavItem } from "./command-palette";
import { NotificationCenter } from "./notification-center";
import { selectClass } from "./ui";

const NAV: NavItem[] = [
  { href: "/director", label: "Director dashboard", icon: Gauge, perm: "executive:read", group: "Overview", keywords: "executive kpi" },
  { href: "/ask", label: "Ask CareFlow", icon: MessageSquareText, perm: "ask:use", group: "Overview", keywords: "ai question analytics" },
  { href: "/", label: "Discharge board", icon: LayoutList, perm: "workflow:read", group: "Discharge", keywords: "workflows board" },
  { href: "/patients", label: "Patients", icon: UserRound, perm: "workflow:read", group: "Discharge", keywords: "admit authorize" },
  { href: "/approvals", label: "Approvals", icon: ClipboardCheck, perm: "approval:decide", group: "Discharge", keywords: "decide approve reject" },
  { href: "/queue", label: "Department queues", icon: ClipboardList, perm: "dept:", group: "Discharge", keywords: "pharmacy lab billing insurance documentation tasks" },
  { href: "/beds", label: "Beds", icon: BedDouble, perm: "workflow:read", group: "Discharge", keywords: "ward occupancy cleaning" },
  { href: "/operations", label: "Operations", icon: BarChart3, perm: "analytics:read", group: "Insights", keywords: "agents export csv" },
  { href: "/audit", label: "Audit trail", icon: History, perm: "audit:read", group: "Insights", keywords: "compliance events" },
  { href: "/integrations", label: "Integrations", icon: Plug, perm: "integration:read", group: "Administration", keywords: "hl7 fhir webhooks keys" },
  { href: "/users", label: "Users", icon: Users, perm: "user:manage", group: "Administration", keywords: "accounts roles" },
];
const GROUPS = ["Overview", "Discharge", "Insights", "Administration"];
const PUBLIC = ["/login", "/setup"];
const COLLAPSE_KEY = "careflow-sidebar-collapsed";

export function Mark({ size = 32 }: { size?: number }) {
  return (
    <svg viewBox="0 0 32 32" width={size} height={size} aria-hidden className="shrink-0">
      <rect width="32" height="32" rx="9" fill="#10243a" />
      <path d="M9 19a7 7 0 0 1 12.5-4.3" stroke="#5fd3c8" strokeWidth="3" fill="none" strokeLinecap="round" />
      <path d="M23 13a7 7 0 0 1-12.5 4.3" stroke="#8fb3f2" strokeWidth="3" fill="none" strokeLinecap="round" />
    </svg>
  );
}

function isActive(href: string, pathname: string) {
  return href === "/" ? pathname === "/" || pathname.startsWith("/workflows") : pathname.startsWith(href);
}

function usePendingCount() {
  const user = useSession((s) => s.user);
  const canDecide = Boolean(user?.permissions.includes("approval:decide"));
  const { data } = useActions();
  if (!canDecide) return 0;
  return (data ?? []).filter((a) => a.status === "pending").length;
}

function NavLinks({ collapsed, onNavigate }: { collapsed?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const perms = useSession((s) => s.user?.permissions ?? []);
  const pending = usePendingCount();
  const items = NAV.filter((n) => allowed(n, perms));
  return (
    <nav aria-label="Main" className="flex flex-col gap-5">
      {GROUPS.map((g) => {
        const group = items.filter((n) => n.group === g);
        if (!group.length) return null;
        return (
          <div key={g}>
            {collapsed ? (
              <div className="mx-auto mb-2 h-px w-6 bg-line-strong" aria-hidden />
            ) : (
              <p className="mb-1.5 px-2.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-faint">{g}</p>
            )}
            <ul className="flex flex-col gap-0.5">
              {group.map(({ href, label, icon: Icon }) => {
                const active = isActive(href, pathname);
                const badge = href === "/approvals" && pending > 0 ? pending : 0;
                return (
                  <li key={href} className="group relative">
                    <Link
                      href={href}
                      onClick={onNavigate}
                      aria-current={active ? "page" : undefined}
                      aria-label={collapsed ? (badge ? `${label}, ${badge} pending` : label) : undefined}
                      className={clsx(
                        "relative flex h-9 items-center gap-2.5 rounded-[9px] text-[13.5px] font-medium transition-colors",
                        collapsed ? "justify-center px-0" : "px-2.5",
                        active ? "bg-surface text-ink shadow-[var(--shadow-card)] ring-1 ring-line" : "text-slate hover:bg-hover hover:text-ink",
                      )}
                    >
                      {active && <span className="absolute inset-y-2 left-0 w-[3px] rounded-r-full bg-tide" aria-hidden />}
                      <Icon className={clsx("size-[17px] shrink-0", active ? "text-tide-deep" : "text-faint group-hover:text-slate")} aria-hidden />
                      {!collapsed && <span className="flex-1 truncate">{label}</span>}
                      {badge > 0 &&
                        (collapsed ? (
                          <span className="absolute right-1.5 top-1.5 size-2 rounded-full bg-violet" aria-hidden />
                        ) : (
                          <span className="tnum rounded-full bg-violet px-1.5 py-px text-[11px] font-bold text-white" aria-label={`${badge} pending`}>
                            {badge}
                          </span>
                        ))}
                    </Link>
                    {collapsed && (
                      <span
                        role="tooltip"
                        className="pointer-events-none absolute left-full top-1/2 z-40 ml-2 hidden -translate-y-1/2 whitespace-nowrap rounded-lg bg-navy px-2.5 py-1.5 text-xs font-medium text-white shadow-lg group-focus-within:block group-hover:block"
                      >
                        {label}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </nav>
  );
}

function Breadcrumbs() {
  const pathname = usePathname();
  const crumbs: { label: string; href?: string }[] = [];
  if (pathname.startsWith("/workflows/")) {
    const [, , id, sub] = pathname.split("/");
    crumbs.push({ label: "Discharge board", href: "/" }, { label: id, href: sub ? `/workflows/${id}` : undefined });
    if (sub === "report") crumbs.push({ label: "Clearance report" });
  } else if (pathname === "/account") {
    crumbs.push({ label: "Account" });
  } else {
    const item = NAV.find((n) => isActive(n.href, pathname));
    if (item) crumbs.push({ label: item.group }, { label: item.label });
  }
  return (
    <nav aria-label="Breadcrumb" className="min-w-0">
      <ol className="flex min-w-0 items-center gap-1 text-[13px]">
        {crumbs.map((c, i) => {
          const last = i === crumbs.length - 1;
          return (
            <li key={`${c.label}-${i}`} className={clsx("flex min-w-0 items-center gap-1", !last && "hidden sm:flex")}>
              {c.href && !last ? (
                <Link href={c.href} className="truncate text-slate hover:text-ink">
                  {c.label}
                </Link>
              ) : (
                <span className={clsx("truncate", last ? "font-semibold text-ink" : "text-faint")} aria-current={last ? "page" : undefined}>
                  {c.label}
                </span>
              )}
              {!last && <ChevronRight className="size-3.5 shrink-0 text-disabled" aria-hidden />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase())
    .join("");
}

function UserMenu({ onShortcuts }: { onShortcuts: () => void }) {
  const { user, signOut } = useSession();
  const qc = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = useState(false);
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
  if (!user) return null;
  const item = "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-ink hover:bg-hover";
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={`Account menu for ${user.name}`}
        className="flex items-center gap-2 rounded-[10px] p-1 hover:bg-hover sm:pr-2"
      >
        <span className="grid size-8 place-items-center rounded-full bg-tide-soft text-xs font-bold text-tide-deep ring-1 ring-tide/20">{initials(user.name)}</span>
        <span className="hidden text-left leading-tight md:block">
          <span className="block max-w-[10rem] truncate text-[13px] font-semibold text-ink">{user.name}</span>
          <span className="block max-w-[10rem] truncate text-[11px] text-slate">{user.role}</span>
        </span>
      </button>
      {open && (
        <div role="menu" className="overlay pop-in absolute right-0 z-40 mt-2 w-60 rounded-2xl p-1.5">
          <div className="px-2.5 pb-2 pt-1.5">
            <p className="truncate text-sm font-semibold text-ink">{user.name}</p>
            <p className="truncate text-xs text-slate">{user.email}</p>
            <p className="mt-1.5 inline-flex rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-semibold text-slate ring-1 ring-line">{user.role}</p>
          </div>
          <div className="my-1 h-px bg-line" />
          <Link role="menuitem" href="/account" onClick={() => setOpen(false)} className={item}>
            <Settings className="size-4 text-faint" aria-hidden /> Account and alerts
          </Link>
          <button
            role="menuitem"
            type="button"
            className={item}
            onClick={() => {
              setOpen(false);
              onShortcuts();
            }}
          >
            <Keyboard className="size-4 text-faint" aria-hidden /> Keyboard shortcuts
          </button>
          <div className="my-1 h-px bg-line" />
          <button
            role="menuitem"
            type="button"
            className={clsx(item, "text-coral-ink")}
            onClick={() => {
              signOut();
              qc.clear();
              router.replace("/login");
            }}
          >
            <LogOut className="size-4" aria-hidden /> Sign out
          </button>
        </div>
      )}
    </div>
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
    <div role="status" className="mb-5 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-tide/20 bg-tide-soft/70 px-4 py-2.5 text-sm text-ink">
      <span className="inline-flex items-center rounded-full bg-surface px-2 py-0.5 text-[11px] font-bold uppercase tracking-[0.06em] text-tide-deep ring-1 ring-tide/20">
        Demo
      </span>
      <span className="text-[13px] text-slate">Fictional hospital with synthetic patients. Try anything; the data resets daily.</span>
      {roles.length > 0 && (
        <label className="ml-auto flex items-center gap-2 whitespace-nowrap text-xs font-semibold text-slate">
          View as
          <select value={user.demoRole ?? ""} disabled={busy} onChange={(e) => switchTo(e.target.value)} className={clsx(selectClass, "h-8 w-auto text-xs font-semibold")}>
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

export function AppShell({ children }: { children: ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  // Read once on the client; the shell renders nothing user-specific until the session has hydrated anyway.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return typeof window !== "undefined" && localStorage.getItem(COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [palette, setPalette] = useState(false);
  const [shortcuts, setShortcuts] = useState(false);
  const pathname = usePathname();
  const router = useRouter();
  const token = useSession((s) => s.token);
  const hydrated = useHydrated();
  const isPublic = PUBLIC.includes(pathname);
  const isPrint = pathname.endsWith("/report");

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, c ? "0" : "1");
      } catch {}
      return !c;
    });
  }, []);

  useEffect(() => {
    if (hydrated && !token && !isPublic) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [hydrated, token, isPublic, pathname, router]);

  useEffect(() => {
    if (isPublic || !token) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((p) => !p);
      } else if (!typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        if (e.key === "/") {
          e.preventDefault();
          setPalette(true);
        } else if (e.key === "?") {
          setShortcuts(true);
        } else if (e.key === "[") {
          toggleCollapsed();
        }
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [isPublic, token, toggleCollapsed]);

  if (isPublic) return <>{children}</>;
  if (!hydrated || !token) return <div className="min-h-screen" aria-busy="true" />;
  if (isPrint) return <main id="main">{children}</main>;

  return (
    <div className="flex min-h-screen">
      <aside
        className={clsx(
          "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-line bg-sidebar transition-[width] duration-200 lg:flex",
          collapsed ? "w-[68px]" : "w-[248px]",
        )}
      >
        <div className={clsx("flex h-14 items-center border-b border-line", collapsed ? "justify-center px-2" : "px-4")}>
          <Link href="/" className="flex items-center gap-2.5" aria-label="CareFlow home">
            <Mark size={28} />
            {!collapsed && <span className="text-[16px] font-bold tracking-[-0.01em] text-ink">CareFlow</span>}
          </Link>
        </div>
        <div className={clsx("scroll-quiet flex-1 overflow-y-auto py-4", collapsed ? "px-2.5" : "px-3")}>
          <NavLinks collapsed={collapsed} />
        </div>
        <div className={clsx("border-t border-line p-3", collapsed && "flex justify-center")}>
          <button
            type="button"
            onClick={toggleCollapsed}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar ( [ )" : "Collapse sidebar ( [ )"}
            className={clsx(
              "flex h-8 items-center gap-2 rounded-[9px] text-xs font-medium text-slate hover:bg-hover hover:text-ink",
              collapsed ? "w-9 justify-center" : "w-full px-2.5",
            )}
          >
            {collapsed ? <ChevronsRight className="size-4" aria-hidden /> : <ChevronsLeft className="size-4" aria-hidden />}
            {!collapsed && "Collapse"}
          </button>
        </div>
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div className="fade-in absolute inset-0 bg-navy/30" onClick={() => setMobileOpen(false)} aria-hidden />
          <div id="mobile-nav" className="slide-in absolute inset-y-0 left-0 flex w-[min(18rem,86vw)] flex-col bg-sidebar shadow-xl" role="dialog" aria-modal="true" aria-label="Navigation">
            <div className="flex h-14 items-center justify-between border-b border-line px-4">
              <span className="flex items-center gap-2.5">
                <Mark size={28} />
                <span className="text-[16px] font-bold text-ink">CareFlow</span>
              </span>
              <button type="button" onClick={() => setMobileOpen(false)} aria-label="Close menu" className="rounded-lg p-2 text-slate hover:bg-hover">
                <X className="size-5" aria-hidden />
              </button>
            </div>
            <div className="scroll-quiet flex-1 overflow-y-auto px-3 py-4">
              <NavLinks onNavigate={() => setMobileOpen(false)} />
            </div>
          </div>
        </div>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b border-line bg-mist/85 px-3 backdrop-blur-md sm:px-6 lg:px-8">
          <button
            type="button"
            onClick={() => setMobileOpen(true)}
            aria-expanded={mobileOpen}
            aria-controls="mobile-nav"
            aria-label="Open menu"
            className="grid size-9 shrink-0 place-items-center rounded-[10px] text-ink hover:bg-hover lg:hidden"
          >
            <Menu className="size-5" aria-hidden />
          </button>
          <Breadcrumbs />
          <div className="ml-auto flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => setPalette(true)}
              className="hidden h-9 w-60 items-center gap-2 rounded-[10px] border border-line-strong bg-surface px-3 text-[13px] text-faint shadow-[0_1px_1px_rgba(16,36,58,0.03)] hover:border-[#b9c6d3] md:flex xl:w-72"
            >
              <Search className="size-4" aria-hidden />
              <span className="flex-1 text-left">Search patients, discharges…</span>
              <Kbd>Ctrl K</Kbd>
            </button>
            <button
              type="button"
              onClick={() => setPalette(true)}
              aria-label="Search"
              className="grid size-9 place-items-center rounded-[10px] text-slate hover:bg-hover hover:text-ink md:hidden"
            >
              <Search className="size-[18px]" aria-hidden />
            </button>
            <NotificationCenter />
            <button
              type="button"
              onClick={() => setShortcuts(true)}
              aria-label="Help and keyboard shortcuts"
              title="Help and keyboard shortcuts"
              className="hidden size-9 place-items-center rounded-[10px] text-slate hover:bg-hover hover:text-ink sm:grid"
            >
              <CircleHelp className="size-[18px]" aria-hidden />
            </button>
            <div className="mx-1 hidden h-6 w-px bg-line sm:block" aria-hidden />
            <UserMenu onShortcuts={() => setShortcuts(true)} />
          </div>
        </header>

        <main id="main" className="mx-auto w-full max-w-[1400px] flex-1 px-4 pb-12 pt-6 sm:px-6 lg:px-8 lg:pt-8">
          <DemoBar />
          {children}
        </main>
      </div>

      {palette && <CommandPalette open onClose={() => setPalette(false)} nav={NAV} />}
      <ShortcutsDialog open={shortcuts} onClose={() => setShortcuts(false)} />
    </div>
  );
}
