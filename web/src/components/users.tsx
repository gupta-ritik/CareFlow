"use client";

import clsx from "clsx";
import { KeyRound, Plus, Search, ShieldCheck, Users as UsersIcon } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { AlertsPanel } from "./alerts";
import { Badge, Button, ConfirmDialog, Dialog, EmptyState, ErrorText, Field, PageHeader, Panel, Section, Skeleton, inputClass, selectClass, toast } from "./ui";
import { changePassword } from "@/lib/api";
import { useCreateUser, useRoles, useUpdateUser, useUsers } from "@/lib/queries";
import { useSession } from "@/lib/session";
import type { UserAccount } from "@/lib/types";

function NewUser({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data: roles } = useRoles();
  const create = useCreateUser();
  const [f, setF] = useState({ name: "", email: "", role: "Discharge coordinator", password: "" });
  const [touched, setTouched] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  const desc = roles?.find((r) => r.name === f.role)?.description;
  const errors = {
    name: f.name.trim().length < 2 ? "Enter their full name." : null,
    email: !/^\S+@\S+\.\S+$/.test(f.email) ? "Enter a valid email address." : null,
    password: f.password.length < 12 ? "Use at least 12 characters." : null,
  };
  const show = (k: keyof typeof errors) => (touched ? errors[k] : null);
  const submit = () => {
    setTouched(true);
    if (Object.values(errors).some(Boolean)) return;
    create.mutate(f, {
      onSuccess: () => {
        toast.success(`Account created for ${f.name}. Share the temporary password with them privately.`);
        setF({ name: "", email: "", role: f.role, password: "" });
        setTouched(false);
        onClose();
      },
    });
  };
  return (
    <Dialog
      open={open}
      onClose={onClose}
      size="lg"
      title="Add a staff account"
      description="Each person gets their own account. Their role decides which queue they work from and what they can approve."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} loading={create.isPending}>
            Create account
          </Button>
        </>
      }
    >
      <form
        noValidate
        className="grid gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Field label="Full name" htmlFor="u-name" error={show("name")} required>
          <input id="u-name" value={f.name} onChange={set("name")} className={inputClass} aria-invalid={Boolean(show("name")) || undefined} />
        </Field>
        <Field label="Email" htmlFor="u-email" hint="They sign in with this." error={show("email")} required>
          <input id="u-email" type="email" value={f.email} onChange={set("email")} className={inputClass} aria-invalid={Boolean(show("email")) || undefined} />
        </Field>
        <Field label="Role" htmlFor="u-role" hint={desc} required>
          <select id="u-role" value={f.role} onChange={set("role")} className={selectClass}>
            {roles?.map((r) => (
              <option key={r.name}>{r.name}</option>
            ))}
          </select>
        </Field>
        <Field label="Temporary password" htmlFor="u-pw" hint="12+ characters, mixing letters with digits or symbols. They change it after signing in." error={show("password")} required>
          <input id="u-pw" type="text" autoComplete="off" value={f.password} onChange={set("password")} className={inputClass} aria-invalid={Boolean(show("password")) || undefined} />
        </Field>
        <button type="submit" className="hidden" aria-hidden tabIndex={-1} />
        <div className="sm:col-span-2">
          <ErrorText error={create.error} />
        </div>
      </form>
    </Dialog>
  );
}

function ResetPassword({ user, onClose }: { user: UserAccount; onClose: () => void }) {
  const update = useUpdateUser();
  const [pw, setPw] = useState("");
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Reset password for ${user.name}`}
      description="Set a temporary password and share it with them privately. This also unlocks the account."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            disabled={pw.length < 12}
            loading={update.isPending}
            onClick={() =>
              update.mutate(
                { id: user.id, body: { password: pw } },
                {
                  onSuccess: () => {
                    toast.success(`Password reset for ${user.name}.`);
                    onClose();
                  },
                },
              )
            }
          >
            Set password
          </Button>
        </>
      }
    >
      <Field label="New temporary password" htmlFor="reset-pw" hint="At least 12 characters.">
        <input id="reset-pw" autoComplete="off" value={pw} onChange={(e) => setPw(e.target.value)} className={inputClass} />
      </Field>
      <div className="mt-3">
        <ErrorText error={update.error} />
      </div>
    </Dialog>
  );
}

function UserRow({ u, onReset }: { u: UserAccount; onReset: () => void }) {
  const me = useSession((s) => s.user?.email === u.email);
  const { data: roles } = useRoles();
  const update = useUpdateUser();
  const [confirm, setConfirm] = useState(false);
  const status = !u.isActive ? (
    <Badge tone="neutral">Deactivated</Badge>
  ) : u.locked ? (
    <Badge tone="danger">Locked</Badge>
  ) : (
    <Badge tone="success">Active</Badge>
  );
  return (
    <tr className={clsx("align-middle", !u.isActive && "bg-surface-2/60")}>
      <td className="px-5 py-3">
        <p className="flex items-center gap-1.5 font-medium text-ink">
          {u.name} {me && <span className="text-xs font-normal text-faint">(you)</span>}
        </p>
        <p className="text-[13px] text-slate">{u.email}</p>
      </td>
      <td className="px-3 py-3">
        <label htmlFor={`role-${u.id}`} className="sr-only">
          Role for {u.name}
        </label>
        <select
          id={`role-${u.id}`}
          value={u.role}
          disabled={me || update.isPending}
          onChange={(e) =>
            update.mutate({ id: u.id, body: { role: e.target.value } }, { onSuccess: () => toast.success(`${u.name} is now ${e.target.value}.`) })
          }
          className={clsx(selectClass, "h-8 min-w-[12rem] text-[13px]")}
          title={me ? "You can't change your own role" : undefined}
        >
          {roles?.map((r) => (
            <option key={r.name}>{r.name}</option>
          ))}
        </select>
      </td>
      <td className="px-3 py-3">{status}</td>
      <td className="tnum px-3 py-3 text-[13px] text-slate">
        {u.lastLoginAt ? new Date(u.lastLoginAt).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "Never"}
      </td>
      <td className="tnum px-3 py-3 text-[13px] text-slate">{new Date(u.createdAt).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })}</td>
      <td className="px-5 py-3">
        <div className="flex flex-wrap items-center justify-end gap-1">
          {u.locked && u.isActive && (
            <Button
              variant="secondary"
              size="sm"
              loading={update.isPending}
              onClick={() => update.mutate({ id: u.id, body: { isActive: true } }, { onSuccess: () => toast.success(`${u.name} is unlocked.`) })}
            >
              Unlock
            </Button>
          )}
          <Button variant="quiet" size="sm" onClick={onReset}>
            <KeyRound className="size-3.5" aria-hidden /> Reset password
          </Button>
          {!me &&
            (u.isActive ? (
              <Button variant="danger" size="sm" onClick={() => setConfirm(true)}>
                Deactivate
              </Button>
            ) : (
              <Button
                variant="secondary"
                size="sm"
                loading={update.isPending}
                onClick={() => update.mutate({ id: u.id, body: { isActive: true } }, { onSuccess: () => toast.success(`${u.name} can sign in again.`) })}
              >
                Reactivate
              </Button>
            ))}
        </div>
        {update.error && !confirm && (
          <div className="mt-2">
            <ErrorText error={update.error} />
          </div>
        )}
        <ConfirmDialog
          open={confirm}
          onClose={() => setConfirm(false)}
          title={`Deactivate ${u.name}?`}
          confirmLabel="Deactivate account"
          tone="dangerSolid"
          busy={update.isPending}
          error={update.error}
          onConfirm={() =>
            update.mutate(
              { id: u.id, body: { isActive: false } },
              {
                onSuccess: () => {
                  setConfirm(false);
                  toast.success(`${u.name} can no longer sign in.`);
                },
              },
            )
          }
        >
          They&apos;re signed out and can&apos;t sign in until reactivated. Their past actions stay in the audit trail.
        </ConfirmDialog>
      </td>
    </tr>
  );
}

export function Users() {
  const { data, isPending, error } = useUsers();
  const { data: roles } = useRoles();
  const welcome = useSearchParams().get("welcome");
  const [adding, setAdding] = useState(Boolean(welcome));
  const [resetFor, setResetFor] = useState<UserAccount | null>(null);
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const term = q.trim().toLowerCase();
  const rows = (data ?? []).filter((u) => (!role || u.role === role) && (!term || u.name.toLowerCase().includes(term) || u.email.toLowerCase().includes(term)));
  const active = (data ?? []).filter((u) => u.isActive).length;
  return (
    <>
      <PageHeader
        title="Users"
        actions={
          <Button onClick={() => setAdding(true)}>
            <Plus className="size-4" aria-hidden /> Add user
          </Button>
        }
      >
        {welcome
          ? "Your hospital is set up. Add an account for each person who will use CareFlow: coordinators, clinicians, ward clerks and each department's staff."
          : "Each person signs in with their own account. Their role decides which queue they work from and what they can approve."}
      </PageHeader>

      <div className="grid grid-cols-1 items-start gap-5 2xl:grid-cols-[minmax(0,1fr)_22rem]">
        <Panel>
          <div className="flex flex-col gap-2 border-b border-line p-3 sm:flex-row sm:items-center sm:justify-between sm:p-4">
            <p className="tnum text-[13px] text-slate">
              {data ? `${active} active of ${data.length} accounts` : "Loading accounts"}
            </p>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative sm:w-60">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
                <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or email" aria-label="Search users" className={clsx(inputClass, "pl-9")} />
              </div>
              <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role" className={clsx(selectClass, "sm:w-52")}>
                <option value="">All roles</option>
                {roles?.map((r) => (
                  <option key={r.name}>{r.name}</option>
                ))}
              </select>
            </div>
          </div>
          {error ? (
            <p className="p-5 text-sm text-coral-ink">{error.message}</p>
          ) : isPending ? (
            <div className="p-4">
              <Skeleton className="h-32" />
            </div>
          ) : rows.length === 0 ? (
            <EmptyState icon={UsersIcon} title="No accounts match">
              Try another name or role.
            </EmptyState>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[860px] text-left text-sm">
                <thead className="bg-surface-2 text-[11px] font-semibold uppercase tracking-[0.06em] text-faint">
                  <tr>
                    <th scope="col" className="px-5 py-2.5 font-semibold">
                      Name
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">
                      Role
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">
                      Status
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">
                      Last sign-in
                    </th>
                    <th scope="col" className="px-3 py-2.5 font-semibold">
                      Created
                    </th>
                    <th scope="col" className="px-5 py-2.5 text-right font-semibold">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {rows.map((u) => (
                    <UserRow key={u.id} u={u} onReset={() => setResetFor(u)} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>

        <Section title="Roles and what they can do" description="Permissions are enforced by the server for every request." bodyClassName="p-0">
          <ul className="divide-y divide-line">
            {(roles ?? []).map((r) => (
              <li key={r.name} className="px-5 py-3">
                <p className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
                  <ShieldCheck className="size-3.5 text-tide-deep" aria-hidden /> {r.name}
                </p>
                <p className="mt-0.5 text-xs leading-relaxed text-slate">{r.description}</p>
              </li>
            ))}
          </ul>
        </Section>
      </div>

      <NewUser open={adding} onClose={() => setAdding(false)} />
      {resetFor && <ResetPassword user={resetFor} onClose={() => setResetFor(null)} />}
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
      <PageHeader title="Account and alerts">Your profile, phone alerts, app installation and password.</PageHeader>
      <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex flex-col gap-5">
          <Panel className="p-5 sm:p-6">
            <h2 className="text-[15px] font-semibold text-ink">Profile</h2>
            <dl className="mt-4 grid grid-cols-1 gap-4 text-sm sm:grid-cols-3">
              <div className="min-w-0">
                <dt className="text-xs text-faint">Name</dt>
                <dd className="mt-0.5 truncate font-medium text-ink">{user?.name}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-xs text-faint">Email</dt>
                <dd className="mt-0.5 truncate font-medium text-ink">{user?.email}</dd>
              </div>
              <div className="min-w-0">
                <dt className="text-xs text-faint">Role</dt>
                <dd className="mt-0.5 font-medium text-ink">{user?.role}</dd>
              </div>
            </dl>
            <p className="mt-4 text-xs text-faint">Your name, email and role are managed by your CareFlow administrator.</p>
          </Panel>
          <AlertsPanel />
        </div>
        {!user?.demo && (
          <Panel className="p-5 sm:p-6">
            <h2 className="text-[15px] font-semibold text-ink">Password</h2>
            <form onSubmit={submit} className="mt-4 flex flex-col gap-4">
              <Field label="Current password" htmlFor="cur">
                <input
                  id="cur"
                  type="password"
                  autoComplete="current-password"
                  value={f.current}
                  onChange={(e) => setF({ ...f, current: e.target.value })}
                  className={inputClass}
                  required
                />
              </Field>
              <Field label="New password" htmlFor="new" hint="At least 12 characters, mixing letters with digits or symbols.">
                <input
                  id="new"
                  type="password"
                  autoComplete="new-password"
                  value={f.next}
                  onChange={(e) => setF({ ...f, next: e.target.value })}
                  className={inputClass}
                  required
                  minLength={12}
                />
              </Field>
              <Field label="Confirm new password" htmlFor="conf">
                <input
                  id="conf"
                  type="password"
                  autoComplete="new-password"
                  value={f.confirm}
                  onChange={(e) => setF({ ...f, confirm: e.target.value })}
                  className={inputClass}
                  required
                  minLength={12}
                />
              </Field>
              <ErrorText error={state.error} />
              {state.done && <p className="rounded-xl bg-tide-soft px-3 py-2 text-sm text-success">Password changed.</p>}
              <Button type="submit" disabled={state.busy} className="w-fit">
                {state.busy ? "Saving" : "Change password"}
              </Button>
            </form>
          </Panel>
        )}
      </div>
    </>
  );
}
