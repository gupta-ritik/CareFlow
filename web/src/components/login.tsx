"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Mark } from "./app-shell";
import { ErrorText, Field, inputClass } from "./ui";
import { login, runSetup, setupStatus } from "@/lib/api";
import { useSession } from "@/lib/session";

function Card({ title, lead, children }: { title: string; lead: string; children: ReactNode }) {
  return (
    <main className="grid min-h-screen place-items-center p-4">
      <div className="glass-strong w-full max-w-[28rem] rounded-[28px] p-6 sm:p-8">
        <div className="flex items-center gap-2.5">
          <Mark />
          <span className="text-lg font-bold text-ink">CareFlow</span>
        </div>
        <h1 className="mt-6 text-2xl font-bold tracking-[-0.02em] text-ink">{title}</h1>
        <p className="mt-1 text-sm leading-relaxed text-slate">{lead}</p>
        {children}
      </div>
    </main>
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

export function Login() {
  const router = useRouter();
  const goNext = useGoNext();
  const signIn = useSession((s) => s.signIn);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setupStatus()
      .then((s) => s.needsSetup && router.replace("/setup"))
      .catch((e) => setError(e));
  }, [router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
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
    <Card title="Sign in" lead="Use the account your administrator created for you.">
      <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
        <Field label="Email" htmlFor="email">
          <input id="email" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} className={inputClass} required />
        </Field>
        <Field label="Password" htmlFor="password">
          <input id="password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} className={inputClass} required />
        </Field>
        <ErrorText error={error} />
        <button type="submit" disabled={busy} className="rounded-xl bg-ink px-4 py-2.5 text-sm font-semibold text-white hover:bg-ink/90 disabled:opacity-60">
          {busy ? "Signing in" : "Sign in"}
        </button>
        <p className="text-xs text-faint">Forgot your password? Ask your CareFlow administrator to reset it.</p>
      </form>
    </Card>
  );
}

export function Setup() {
  const router = useRouter();
  const signIn = useSession((s) => s.signIn);
  const [f, setF] = useState({ hospitalName: "", adminName: "", adminEmail: "", password: "", confirm: "" });
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setupStatus()
      .then((s) => !s.needsSetup && router.replace("/login"))
      .catch((e) => setError(e));
  }, [router]);

  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement>) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (f.password !== f.confirm) return setError(new Error("The two passwords don't match."));
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

  return (
    <Card title="Set up CareFlow" lead="Name your hospital and create the first administrator account. You'll add staff accounts next.">
      <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
        <Field label="Hospital name" htmlFor="hospital">
          <input id="hospital" value={f.hospitalName} onChange={set("hospitalName")} className={inputClass} required minLength={2} />
        </Field>
        <Field label="Your name" htmlFor="name">
          <input id="name" autoComplete="name" value={f.adminName} onChange={set("adminName")} className={inputClass} required minLength={2} />
        </Field>
        <Field label="Your email" htmlFor="email">
          <input id="email" type="email" autoComplete="username" value={f.adminEmail} onChange={set("adminEmail")} className={inputClass} required />
        </Field>
        <Field label="Password" htmlFor="password" hint="At least 10 characters.">
          <input id="password" type="password" autoComplete="new-password" value={f.password} onChange={set("password")} className={inputClass} required minLength={10} />
        </Field>
        <Field label="Confirm password" htmlFor="confirm">
          <input id="confirm" type="password" autoComplete="new-password" value={f.confirm} onChange={set("confirm")} className={inputClass} required minLength={10} />
        </Field>
        <ErrorText error={error} />
        <button type="submit" disabled={busy} className="rounded-xl bg-ink px-4 py-2.5 text-sm font-semibold text-white hover:bg-ink/90 disabled:opacity-60">
          {busy ? "Setting up" : "Create hospital and sign in"}
        </button>
      </form>
    </Card>
  );
}
