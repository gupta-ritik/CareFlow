"use client";

import { Plus } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { Button, ErrorText, Field, PageHeader, Panel, Skeleton, inputClass } from "./ui";
import { changePassword } from "@/lib/api";
import { useCreateUser, useRoles, useUpdateUser, useUsers } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { UserAccount } from "@/lib/types";

function NewUser({ onDone }: { onDone: () => void }) {
  const { data: roles } = useRoles();
  const create = useCreateUser();
  const [f, setF] = useState({ name: "", email: "", role: "Discharge coordinator", password: "" });
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const desc = roles?.find((r) => r.name === f.role)?.description;
  return (
    <Panel strong className="mb-5 p-5 sm:p-6">
      <h2 className="text-lg font-bold text-ink">Add a staff account</h2>
      <form
        className="mt-4 grid gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate(f, { onSuccess: onDone });
        }}
      >
        <Field label="Full name" htmlFor="u-name">
          <input id="u-name" value={f.name} onChange={set("name")} className={inputClass} required minLength={2} />
        </Field>
        <Field label="Email" htmlFor="u-email" hint="They sign in with this.">
          <input id="u-email" type="email" value={f.email} onChange={set("email")} className={inputClass} required />
        </Field>
        <Field label="Role" htmlFor="u-role" hint={desc}>
          <select id="u-role" value={f.role} onChange={set("role")} className={inputClass}>
            {roles?.map((r) => (
              <option key={r.name}>{r.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Temporary password" htmlFor="u-pw" hint="At least 12 characters, mixing letters with digits or symbols. Ask them to change it after signing in.">
          <input id="u-pw" type="text" autoComplete="off" value={f.password} onChange={set("password")} className={inputClass} required minLength={12} />
        </Field>
        <div className="flex flex-col gap-3 sm:col-span-2">
          <ErrorText error={create.error} />
          <div className="flex gap-2">
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? "Creating" : "Create account"}
            </Button>
            <Button variant="secondary" onClick={onDone}>
              Cancel
            </Button>
          </div>
        </div>
      </form>
    </Panel>
  );
}

function UserRow({ u }: { u: UserAccount }) {
  const me = useSession((s) => s.user?.email === u.email);
  const { data: roles } = useRoles();
  const update = useUpdateUser();
  const [pw, setPw] = useState<string | null>(null);
  return (
    <li className="grid gap-3 px-4 py-3.5 md:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_auto] md:items-center">
      <div className="min-w-0">
        <p className="truncate font-semibold text-ink">
          {u.name} {me && <span className="text-xs font-normal text-faint">(you)</span>}
          {!u.isActive && <span className="ml-1.5 rounded-full bg-ink/[0.06] px-2 py-0.5 text-xs font-semibold text-slate">Deactivated</span>}
          {u.locked && <span className="ml-1.5 rounded-full bg-coral-soft px-2 py-0.5 text-xs font-semibold text-coral">Locked</span>}
        </p>
        <p className="truncate text-sm text-slate">
          {u.email}
          <span className="text-faint"> · {u.lastLoginAt ? `last sign-in ${new Date(u.lastLoginAt).toLocaleString("en-IN")}` : "never signed in"}</span>
        </p>
      </div>
      <div>
        <label htmlFor={`role-${u.id}`} className="sr-only">
          Role for {u.name}
        </label>
        <select
          id={`role-${u.id}`}
          value={u.role}
          disabled={me || update.isPending}
          onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } })}
          className={inputClass}
        >
          {roles?.map((r) => (
            <option key={r.name}>{r.name}</option>
          ))}
        </select>
      </div>
      <div className="flex flex-wrap items-center gap-2 md:justify-end">
        {pw === null ? (
          <Button variant="quiet" onClick={() => setPw("")}>
            Reset password
          </Button>
        ) : (
          <>
            <label htmlFor={`pw-${u.id}`} className="sr-only">
              New password
            </label>
            <input id={`pw-${u.id}`} value={pw} onChange={(e) => setPw(e.target.value)} placeholder="New password (12+)" className={`${inputClass} w-44`} />
            <Button disabled={pw.length < 12 || update.isPending} onClick={() => update.mutate({ id: u.id, body: { password: pw } }, { onSuccess: () => setPw(null) })}>
              Save
            </Button>
          </>
        )}
        {u.locked && u.isActive && (
          <Button variant="secondary" disabled={update.isPending} onClick={() => update.mutate({ id: u.id, body: { isActive: true } })}>
            Unlock
          </Button>
        )}
        {!me && (
          <Button variant={u.isActive ? "danger" : "secondary"} disabled={update.isPending} onClick={() => update.mutate({ id: u.id, body: { isActive: !u.isActive } })}>
            {u.isActive ? "Deactivate" : "Reactivate"}
          </Button>
        )}
      </div>
      {update.error && (
        <div className="md:col-span-3">
          <ErrorText error={update.error} />
        </div>
      )}
    </li>
  );
}

export function Users() {
  const { data, isPending, error } = useUsers();
  const welcome = useSearchParams().get("welcome");
  const [adding, setAdding] = useState(Boolean(welcome));
  return (
    <>
      <PageHeader title="Users">
        {welcome ? (
          <p>Your hospital is set up. Add an account for each person who will use CareFlow: coordinators, clinicians, ward clerks and each department&apos;s staff.</p>
        ) : (
          <p>Each person signs in with their own account. Their role decides which queue they work from and what they can approve.</p>
        )}
      </PageHeader>
      {adding ? (
        <NewUser onDone={() => setAdding(false)} />
      ) : (
        <div className="mb-4">
          <Button onClick={() => setAdding(true)}>
            <Plus className="size-4" aria-hidden /> Add user
          </Button>
        </div>
      )}
      <Panel className="p-2">
        {error ? (
          <p className="p-4 text-sm text-coral">{error.message}</p>
        ) : isPending ? (
          <Skeleton className="h-32" />
        ) : (
          <ul className="divide-y divide-white/70">
            {data.map((u) => (
              <UserRow key={u.id} u={u} />
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}

export function Account() {
  const user = useSession((s) => s.user);
  const [f, setF] = useState({ current: "", next: "", confirm: "" });
  const [state, setState] = useState<{ busy?: boolean; error?: unknown; done?: boolean }>({});
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (f.next !== f.confirm) return setState({ error: new Error("The new passwords don't match.") });
    setState({ busy: true });
    try {
      await changePassword(f.current, f.next);
      setF({ current: "", next: "", confirm: "" });
      setState({ done: true });
    } catch (err) {
      setState({ error: err });
    }
  };
  return (
    <>
      <PageHeader title="Your account">
        <p>
          {user?.name}, {user?.role}. {user?.email}
        </p>
      </PageHeader>
      <Panel className="max-w-lg p-5 sm:p-6">
        <h2 className="text-lg font-bold text-ink">Change password</h2>
        <form onSubmit={submit} className="mt-4 flex flex-col gap-4">
          <Field label="Current password" htmlFor="cur">
            <input id="cur" type="password" autoComplete="current-password" value={f.current} onChange={(e) => setF({ ...f, current: e.target.value })} className={inputClass} required />
          </Field>
          <Field label="New password" htmlFor="new" hint="At least 12 characters, mixing letters with digits or symbols.">
            <input id="new" type="password" autoComplete="new-password" value={f.next} onChange={(e) => setF({ ...f, next: e.target.value })} className={inputClass} required minLength={12} />
          </Field>
          <Field label="Confirm new password" htmlFor="conf">
            <input id="conf" type="password" autoComplete="new-password" value={f.confirm} onChange={(e) => setF({ ...f, confirm: e.target.value })} className={inputClass} required minLength={12} />
          </Field>
          <ErrorText error={state.error} />
          {state.done && <p className="rounded-xl bg-tide-soft px-3 py-2 text-sm text-[#0a6b64]">Password changed.</p>}
          <Button type="submit" disabled={state.busy} className="w-fit">
            {state.busy ? "Saving" : "Change password"}
          </Button>
        </form>
      </Panel>
    </>
  );
}
