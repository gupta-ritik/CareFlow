"use client";

import clsx from "clsx";
import {
  ArrowRight,
  BedDouble,
  Building2,
  Check,
  ClipboardCheck,
  ClipboardList,
  Eye,
  EyeOff,
  FileSearch,
  FlaskConical,
  Lock,
  Pill,
  Play,
  Receipt,
  ShieldCheck,
  Stethoscope,
  type LucideIcon,
} from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Mark } from "./app-shell";
import { Button, Dialog, ErrorText, Field, inputClass } from "./ui";
import { demoInfo, demoLogin, login, runSetup, setupStatus } from "@/lib/api";
import { useSession } from "@/lib/session";
import type { DemoInfo } from "@/lib/types";

/** Demo role cards: icon, colour and a short, accurate summary of what the role does in CareFlow. */
const ROLE_META: Record<string, { icon: LucideIcon; blurb: string; tile: string }> = {
  director: { icon: Building2, blurb: "Overview, performance & approvals", tile: "bg-tide-soft text-tide-deep" },
  coordinator: { icon: ClipboardCheck, blurb: "Discharge planning & approvals", tile: "bg-violet-soft text-violet" },
  clinician: { icon: Stethoscope, blurb: "Authorizes discharge, documentation", tile: "bg-harbor-soft text-harbor" },
  "ward-clerk": { icon: BedDouble, blurb: "Admissions, beds & documents", tile: "bg-[#e0f4f7] text-[#0e7490]" },
  pharmacy: { icon: Pill, blurb: "Discharge medicines & reconciliation", tile: "bg-[#fde8f0] text-[#be185d]" },
  laboratory: { icon: FlaskConical, blurb: "Lab reports & acknowledgements", tile: "bg-[#efe9fb] text-[#6d28d9]" },
  insurance: { icon: ShieldCheck, blurb: "Payer authorization & queries", tile: "bg-[#fdeedd] text-[#c2410c]" },
  billing: { icon: Receipt, blurb: "Final bill & settlement", tile: "bg-amber-soft text-amber-ink" },
  auditor: { icon: FileSearch, blurb: "Compliance & audit, read-only", tile: "bg-success-soft text-success" },
};

/** Two-column frame: brand story on the left (desktop), the task on the right. */
function AuthFrame({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
      <aside className="relative hidden overflow-hidden bg-navy text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div
          aria-hidden
          className="pointer-events-none absolute -right-32 -top-32 size-[28rem] rounded-full opacity-40"
          style={{ background: "radial-gradient(circle, rgba(13,148,136,0.55), rgba(13,148,136,0) 70%)" }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.06]"
          style={{ backgroundImage: "linear-gradient(#fff 1px, transparent 1px), linear-gradient(90deg, #fff 1px, transparent 1px)", backgroundSize: "40px 40px" }}
        />
        <div className="relative flex items-center gap-3">
          <span className="rounded-[11px] bg-white/10 p-1 ring-1 ring-white/15">
            <Mark size={30} />
          </span>
          <span className="text-lg font-bold tracking-[-0.01em]">CareFlow</span>
        </div>
        <div className="relative max-w-md">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-[#5fd3c8]">Discharge operations</p>
          <h2 className="mt-3 text-[32px] font-bold leading-[1.15] tracking-[-0.02em]">Every department clear. Every bed back sooner.</h2>
          <p className="mt-4 text-[15px] leading-relaxed text-white/70">
            CareFlow coordinates documentation, pharmacy, laboratory, insurance and billing the moment a clinician authorizes discharge, and asks the
            right person before anything risky happens.
          </p>
          <ul className="mt-8 flex flex-col gap-3 text-sm text-white/80">
            {[
              "Agents propose; people approve medium- and high-risk actions",
              "Every action cites its evidence and is verified in the source system",
              "Administrative only: never a clinical decision or a medicine change",
            ].map((t) => (
              <li key={t} className="flex items-start gap-2.5">
                <ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#5fd3c8]" aria-hidden />
                {t}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-white/45">Not a certified medical device. Use synthetic data unless your deployment is approved for patient data.</p>
      </aside>
      <main id="main" className="flex items-start justify-center px-4 py-10 sm:px-8 lg:items-center lg:py-12">
        <div className="w-full max-w-[540px]">
          <div className="mb-8 flex items-center gap-2.5 lg:hidden">
            <Mark size={30} />
            <span className="text-lg font-bold text-ink">CareFlow</span>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}

function useGoNext() {
  const router = useRouter();
  const params = useSearchParams();
  return () => {
    const next = params.get("next");
    router.replace(next && next.startsWith("/") && !["/login", "/setup"].includes(next) ? next : "/");
  };
}

function DemoRoles({ info }: { info: Extract<DemoInfo, { enabled: true }> }) {
  const router = useRouter();
  const signIn = useSession((s) => s.signIn);
  const [selected, setSelected] = useState(info.roles[0]?.key ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const chosen = info.roles.find((r) => r.key === selected);

  const enter = async (key = selected) => {
    setBusy(true);
    setError(null);
    try {
      const r = await demoLogin(key);
      signIn(r.accessToken, r.user);
      router.replace(r.home);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  const onKey = (e: React.KeyboardEvent, i: number) => {
    if (e.key === "Enter") {
      e.preventDefault();
      void enter(info.roles[i].key);
      return;
    }
    const cols = window.matchMedia("(min-width: 640px)").matches ? 2 : 1;
    const step = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols }[e.key];
    if (step === undefined) return;
    e.preventDefault();
    const next = Math.min(info.roles.length - 1, Math.max(0, i + step));
    setSelected(info.roles[next].key);
    refs.current[next]?.focus();
  };

  return (
    <section aria-labelledby="demo-title">
      <div className="flex flex-col gap-4 rounded-2xl border border-tide/20 bg-gradient-to-r from-tide-soft/80 to-[#eef8f6] p-4 sm:flex-row sm:items-center">
        <span className="grid size-11 shrink-0 place-items-center rounded-full bg-tide-deep text-white shadow-[0_4px_10px_-2px_rgba(8,127,117,0.45)]">
          <Play className="ml-0.5 size-[18px] fill-current" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <h2 id="demo-title" className="text-[16px] font-bold text-ink">
            Try the live demo
          </h2>
          <p className="mt-0.5 text-[13px] leading-relaxed text-slate">
            No account needed. A fictional hospital with fresh data, reset every {info.resetHours} hours.
          </p>
        </div>
        <Button variant="secondary" className="shrink-0 border-tide-deep text-tide-deep hover:bg-surface" loading={busy} onClick={() => enter()}>
          Explore Demo {!busy && <ArrowRight className="size-4" aria-hidden />}
        </Button>
      </div>
      {info.preparing && (
        <p className="mt-3 rounded-lg bg-amber-soft px-3 py-2 text-xs font-medium text-amber-ink">Demo data is being refreshed; some screens fill in over the next minute.</p>
      )}

      <h2 className="mt-7 text-[22px] font-bold tracking-[-0.01em] text-ink">Choose your role</h2>
      <p className="mt-1 text-sm text-slate">Explore CareFlow as any role. Pick one, then select Explore Demo, or press Enter.</p>

      <div role="radiogroup" aria-label="Demo role" className="mt-4 grid grid-cols-1 gap-2.5 sm:grid-cols-2">
        {info.roles.map((r, i) => {
          const meta = ROLE_META[r.key] ?? { icon: ClipboardList, blurb: r.description, tile: "bg-surface-2 text-slate" };
          const Icon = meta.icon;
          const on = r.key === selected;
          return (
            <button
              key={r.key}
              ref={(el) => {
                refs.current[i] = el;
              }}
              type="button"
              role="radio"
              aria-checked={on}
              aria-describedby={`role-${r.key}-desc`}
              tabIndex={on ? 0 : -1}
              onKeyDown={(e) => onKey(e, i)}
              onClick={() => setSelected(r.key)}
              onDoubleClick={() => enter(r.key)}
              title={r.description}
              className={clsx(
                "relative flex items-center gap-3 rounded-xl border p-3 text-left transition-all",
                on
                  ? "border-tide-deep bg-tide-deep text-white shadow-[0_6px_16px_-6px_rgba(8,127,117,0.55)]"
                  : "border-line bg-surface shadow-[var(--shadow-card)] hover:border-line-strong hover:shadow-[var(--shadow-raised)]",
              )}
            >
              <span className={clsx("grid size-10 shrink-0 place-items-center rounded-[10px]", on ? "bg-white/15 text-white" : meta.tile)}>
                <Icon className="size-5" aria-hidden />
              </span>
              <span className="min-w-0 flex-1 pr-5">
                <span className={clsx("block text-[14px] font-semibold leading-tight", on ? "text-white" : "text-ink")}>{r.role}</span>
                <span id={`role-${r.key}-desc`} className={clsx("mt-0.5 line-clamp-2 block text-xs leading-snug", on ? "text-white/85" : "text-slate")}>
                  {meta.blurb}
                </span>
              </span>
              {on && (
                <span className="absolute right-2.5 top-2.5 grid size-5 place-items-center rounded-full bg-white text-tide-deep" aria-hidden>
                  <Check className="size-3.5" strokeWidth={3} />
                </span>
              )}
            </button>
          );
        })}
      </div>
      {chosen && <p className="sr-only" aria-live="polite">{`${chosen.role} selected. ${chosen.description}.`}</p>}
      <div className="mt-3">
        <ErrorText error={error} />
      </div>
    </section>
  );
}

function PasswordInput({ id, value, onChange, autoComplete, invalid, minLength }: { id: string; value: string; onChange: (v: string) => void; autoComplete: string; invalid?: boolean; minLength?: number }) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        id={id}
        type={show ? "text" : "password"}
        autoComplete={autoComplete}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={invalid || undefined}
        aria-describedby={invalid ? `${id}-error` : undefined}
        minLength={minLength}
        className={clsx(inputClass, "h-10 pr-10")}
        required
      />
      <button
        type="button"
        onClick={() => setShow((s) => !s)}
        aria-label={show ? "Hide password" : "Show password"}
        aria-pressed={show}
        className="absolute right-1.5 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-md text-faint hover:bg-hover hover:text-ink"
      >
        {show ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
      </button>
    </div>
  );
}

export function Login() {
  const router = useRouter();
  const goNext = useGoNext();
  const signIn = useSession((s) => s.signIn);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState<DemoInfo | null>(null);
  const [forgot, setForgot] = useState(false);

  useEffect(() => {
    setupStatus()
      .then((s) => s.needsSetup && router.replace("/setup"))
      .catch((e) => setError(e));
    demoInfo()
      .then(setDemo)
      .catch(() => setDemo(null)); // an older backend without demo mode: just show the sign-in form
  }, [router]);

  const emailError = touched && !/^\S+@\S+\.\S+$/.test(email) ? "Enter the email address of your CareFlow account." : null;
  const passwordError = touched && !password ? "Enter your password." : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (!/^\S+@\S+\.\S+$/.test(email) || !password) return;
    setBusy(true);
    setError(null);
    try {
      const r = await login(email, password);
      signIn(r.accessToken, r.user);
      goNext();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <AuthFrame>
      <h1 className="sr-only">Sign in to CareFlow</h1>
      {demo?.enabled && (
        <>
          <DemoRoles info={demo} />
          <div className="my-7 flex items-center gap-3 text-xs font-semibold uppercase tracking-[0.08em] text-faint">
            <span className="h-px flex-1 bg-line" />
            or sign in with your account
            <span className="h-px flex-1 bg-line" />
          </div>
        </>
      )}
      {!demo?.enabled && (
        <>
          <p className="text-[28px] font-bold leading-tight tracking-[-0.02em] text-ink" aria-hidden>
            Sign in
          </p>
          <p className="mt-1.5 text-sm text-slate">Use the account your hospital administrator created for you.</p>
        </>
      )}

      <form onSubmit={submit} noValidate className={clsx("flex flex-col gap-4", !demo?.enabled && "mt-7")}>
        <Field label="Work email" htmlFor="email" error={emailError}>
          <input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={Boolean(emailError) || undefined}
            aria-describedby={emailError ? "email-error" : undefined}
            className={clsx(inputClass, "h-10")}
            placeholder="name@hospital.in"
            required
          />
        </Field>
        <div>
          <Field label="Password" htmlFor="password" error={passwordError}>
            <PasswordInput id="password" value={password} onChange={setPassword} autoComplete="current-password" invalid={Boolean(passwordError)} />
          </Field>
          <button type="button" onClick={() => setForgot(true)} className="mt-2 text-[13px] font-semibold text-tide-deep hover:underline">
            Forgot password?
          </button>
        </div>
        <ErrorText error={error} />
        <Button type="submit" className="h-10 w-full" loading={busy}>
          {busy ? "Signing in" : "Sign in"}
        </Button>
        <p className="flex items-center justify-center gap-1.5 text-xs text-faint">
          <Lock className="size-3.5" aria-hidden /> Encrypted connection · five wrong attempts lock the account for 15 minutes
        </p>
      </form>

      <Dialog
        open={forgot}
        onClose={() => setForgot(false)}
        title="Reset your password"
        description="For security, CareFlow passwords are reset by your hospital's CareFlow administrator, who can also unlock a locked account."
        footer={
          <Button variant="secondary" onClick={() => setForgot(false)}>
            Got it
          </Button>
        }
      >
        <ol className="flex list-decimal flex-col gap-1.5 pl-5 text-sm text-slate">
          <li>Ask your administrator to open Users and choose Reset password for your account.</li>
          <li>Sign in with the temporary password they give you.</li>
          <li>Change it under Account → Password.</li>
        </ol>
      </Dialog>
    </AuthFrame>
  );
}

export function Setup() {
  const router = useRouter();
  const signIn = useSession((s) => s.signIn);
  const [f, setF] = useState({ hospitalName: "", adminName: "", adminEmail: "", password: "", confirm: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    setupStatus()
      .then((s) => !s.needsSetup && router.replace("/login"))
      .catch((e) => setError(e));
  }, [router]);

  const set = (k: keyof typeof f) => (v: string) => setF({ ...f, [k]: v });
  const errors = {
    hospitalName: f.hospitalName.trim().length < 2 ? "Enter the hospital's name." : null,
    adminName: f.adminName.trim().length < 2 ? "Enter your full name." : null,
    adminEmail: !/^\S+@\S+\.\S+$/.test(f.adminEmail) ? "Enter a valid email address." : null,
    password: f.password.length < 12 ? "Use at least 12 characters." : null,
    confirm: f.confirm !== f.password ? "The two passwords don't match." : null,
  };
  const show = (k: keyof typeof errors) => (touched ? errors[k] : null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setTouched(true);
    if (Object.values(errors).some(Boolean)) return;
    setBusy(true);
    setError(null);
    try {
      const r = await runSetup({ hospitalName: f.hospitalName, adminName: f.adminName, adminEmail: f.adminEmail, password: f.password });
      signIn(r.accessToken, r.user);
      router.replace("/users?welcome=1");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const input = (k: "hospitalName" | "adminName" | "adminEmail", props: React.InputHTMLAttributes<HTMLInputElement>) => (
    <input
      {...props}
      value={f[k]}
      onChange={(e) => set(k)(e.target.value)}
      aria-invalid={Boolean(show(k)) || undefined}
      aria-describedby={show(k) ? `${props.id}-error` : undefined}
      className={clsx(inputClass, "h-10")}
      required
    />
  );

  return (
    <AuthFrame>
      <p className="text-xs font-semibold uppercase tracking-[0.08em] text-tide-deep">First-time setup</p>
      <h1 className="mt-1 text-[28px] font-bold leading-tight tracking-[-0.02em] text-ink">Set up CareFlow</h1>
      <p className="mt-1.5 text-sm text-slate">Name your hospital and create the first administrator account. You&apos;ll add staff accounts next.</p>
      <form onSubmit={submit} noValidate className="mt-7 flex flex-col gap-4">
        <Field label="Hospital name" htmlFor="hospital" error={show("hospitalName")} required>
          {input("hospitalName", { id: "hospital" })}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Your name" htmlFor="name" error={show("adminName")} required>
            {input("adminName", { id: "name", autoComplete: "name" })}
          </Field>
          <Field label="Your email" htmlFor="email" error={show("adminEmail")} required>
            {input("adminEmail", { id: "email", type: "email", autoComplete: "username" })}
          </Field>
        </div>
        <Field label="Password" htmlFor="password" hint="At least 12 characters, mixing letters with digits or symbols." error={show("password")} required>
          <PasswordInput id="password" value={f.password} onChange={set("password")} autoComplete="new-password" invalid={Boolean(show("password"))} minLength={12} />
        </Field>
        <Field label="Confirm password" htmlFor="confirm" error={show("confirm")} required>
          <PasswordInput id="confirm" value={f.confirm} onChange={set("confirm")} autoComplete="new-password" invalid={Boolean(show("confirm"))} minLength={12} />
        </Field>
        <ErrorText error={error} />
        <Button type="submit" className="h-10 w-full" loading={busy}>
          {busy ? "Setting up" : "Create hospital and sign in"}
        </Button>
      </form>
    </AuthFrame>
  );
}
