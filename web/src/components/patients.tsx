"use client";

import clsx from "clsx";
import { Archive, FileText, Plus, Search, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { Button, ErrorText, Field, PageHeader, Panel, Skeleton, WorkflowPill, inputClass } from "./ui";
import { setupStatus, type NewPatient } from "@/lib/api";
import { DEPT_LABEL, duration, minutesSince } from "@/lib/format";
import { useAdmit, useArchivePatient, useAuthorize, useImportFhir, usePatients, useUpdatePatient } from "@/lib/queries";
import { useCan } from "@/lib/session";
import type { PatientRecord } from "@/lib/types";

const PAYER_TYPES = [
  { value: "self_pay", label: "Self-pay" },
  { value: "cashless", label: "Cashless insurance (TPA)" },
  { value: "government", label: "Government scheme (CGHS, PM-JAY, ...)" },
];

const EMPTY: NewPatient = { displayName: "", mrn: "", ward: "", bed: "", payer: "", payerType: "self_pay", attending: "", hasLabOrders: false, consentForCoordination: true };

function AdmitForm({ onDone }: { onDone: () => void }) {
  const admit = useAdmit();
  const importFhir = useImportFhir();
  const [mode, setMode] = useState<"manual" | "fhir">("manual");
  const [fhirReady, setFhirReady] = useState(false);
  const [f, setF] = useState<NewPatient>(EMPTY);
  const [fhir, setFhir] = useState({ patientId: "", encounterId: "" });

  useEffect(() => {
    setupStatus().then((s) => setFhirReady(s.fhirConfigured)).catch(() => {});
  }, []);

  const set = (k: keyof NewPatient) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value });

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (mode === "manual") admit.mutate(f, { onSuccess: onDone });
    else
      importFhir.mutate(
        { ...fhir, payer: f.payer, payerType: f.payerType, attending: f.attending, consentForCoordination: f.consentForCoordination },
        { onSuccess: onDone },
      );
  };
  const busy = admit.isPending || importFhir.isPending;

  return (
    <Panel strong className="p-5 sm:p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-ink">Admit a patient</h2>
          <p className="mt-1 text-sm text-slate">CareFlow creates a work item for each department this patient will need before discharge.</p>
        </div>
        <Button variant="quiet" onClick={onDone} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      <div role="tablist" aria-label="How to add" className="mt-4 flex w-fit gap-1 rounded-full bg-ink/[0.05] p-1">
        {(["manual", "fhir"] as const).map((m) => (
          <button
            key={m}
            role="tab"
            type="button"
            aria-selected={mode === m}
            onClick={() => setMode(m)}
            className={clsx("rounded-full px-3 py-1.5 text-sm font-semibold", mode === m ? "bg-white text-ink shadow-sm" : "text-slate hover:text-ink")}
          >
            {m === "manual" ? "Enter details" : "Import from FHIR"}
          </button>
        ))}
      </div>

      <form onSubmit={submit} className="mt-5 grid gap-4 sm:grid-cols-2">
        {mode === "manual" ? (
          <>
            <Field label="Full name" htmlFor="name">
              <input id="name" value={f.displayName} onChange={set("displayName")} className={inputClass} required minLength={2} />
            </Field>
            <Field label="MRN" htmlFor="mrn" hint="Hospital medical record number. Must be unique.">
              <input id="mrn" value={f.mrn} onChange={set("mrn")} className={inputClass} required minLength={2} />
            </Field>
            <Field label="Ward" htmlFor="ward">
              <input id="ward" value={f.ward} onChange={set("ward")} className={inputClass} required placeholder="Ward 3A" />
            </Field>
            <Field label="Bed" htmlFor="bed">
              <input id="bed" value={f.bed} onChange={set("bed")} className={inputClass} placeholder="Bed 12" />
            </Field>
          </>
        ) : (
          <>
            {!fhirReady && (
              <p className="rounded-xl bg-amber-soft/80 px-3 py-2 text-sm text-ink sm:col-span-2">
                No FHIR server is connected yet. Your administrator sets <code>CAREFLOW_FHIR_BASE_URL</code> on the server; until then, enter details manually.
              </p>
            )}
            <Field label="FHIR Patient ID" htmlFor="fp">
              <input id="fp" value={fhir.patientId} onChange={(e) => setFhir({ ...fhir, patientId: e.target.value })} className={inputClass} required />
            </Field>
            <Field label="FHIR Encounter ID" htmlFor="fe" hint="The current inpatient encounter.">
              <input id="fe" value={fhir.encounterId} onChange={(e) => setFhir({ ...fhir, encounterId: e.target.value })} className={inputClass} required />
            </Field>
          </>
        )}
        <Field label="Attending doctor" htmlFor="att">
          <input id="att" value={f.attending} onChange={set("attending")} className={inputClass} required minLength={2} />
        </Field>
        <Field label="Payment type" htmlFor="ptype">
          <select id="ptype" value={f.payerType} onChange={set("payerType")} className={inputClass}>
            {PAYER_TYPES.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label={f.payerType === "self_pay" ? "Payer" : "Insurer or scheme"} htmlFor="payer">
          <input id="payer" value={f.payer} onChange={set("payer")} className={inputClass} required minLength={2} placeholder={f.payerType === "self_pay" ? "Self-pay" : "Star Health"} />
        </Field>
        <div className="flex flex-col justify-end gap-2 pb-1">
          {mode === "manual" && (
            <label className="flex items-center gap-2 text-sm text-ink">
              <input type="checkbox" checked={f.hasLabOrders} onChange={set("hasLabOrders")} className="size-4" /> Has lab orders during this stay
            </label>
          )}
          <label className="flex items-center gap-2 text-sm text-ink">
            <input type="checkbox" checked={f.consentForCoordination} onChange={set("consentForCoordination")} className="size-4" /> Patient consented to discharge coordination
          </label>
        </div>
        <div className="flex flex-col gap-3 sm:col-span-2">
          <ErrorText error={admit.error || importFhir.error} />
          <div className="flex gap-2">
            <Button type="submit" disabled={busy}>
              {busy ? "Saving" : mode === "manual" ? "Admit patient" : "Import and admit"}
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

function AuthorizeButton({ p }: { p: PatientRecord }) {
  const authorize = useAuthorize();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  if (!open)
    return (
      <Button onClick={() => setOpen(true)} className="whitespace-nowrap">
        Authorize discharge
      </Button>
    );
  return (
    <div className="flex w-full flex-col gap-2 rounded-2xl border border-white/80 bg-white/70 p-3 sm:w-80">
      <p className="text-sm text-ink">
        You&apos;re confirming <span className="font-semibold">{p.displayName}</span> is clinically fit to leave. CareFlow then starts administrative clearance.
      </p>
      <label htmlFor={`note-${p.ref}`} className="sr-only">
        Note
      </label>
      <input id={`note-${p.ref}`} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (optional)" className={inputClass} />
      <ErrorText error={authorize.error} />
      <div className="flex gap-2">
        <Button variant="good" disabled={authorize.isPending} onClick={() => authorize.mutate({ ref: p.ref, note }, { onSuccess: () => setOpen(false) })}>
          {authorize.isPending ? "Starting clearance" : "Confirm authorization"}
        </Button>
        <Button variant="quiet" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function ConsentToggle({ p }: { p: PatientRecord }) {
  const update = useUpdatePatient();
  return (
    <button
      type="button"
      disabled={update.isPending}
      onClick={() => update.mutate({ ref: p.ref, body: { consentForCoordination: true } })}
      className="text-xs font-semibold text-harbor hover:underline"
    >
      Record consent
    </button>
  );
}

/** Administrator only: hide a test patient everywhere. Nothing is deleted; the archive is in the audit trail. */
function ArchiveButton({ p }: { p: PatientRecord }) {
  const archive = useArchivePatient();
  const [open, setOpen] = useState(false);
  if (!open)
    return (
      <Button variant="quiet" onClick={() => setOpen(true)} className="whitespace-nowrap" title="Archive this patient">
        <Archive className="size-4" aria-hidden /> Archive
      </Button>
    );
  return (
    <div className="flex w-full flex-col gap-2 rounded-2xl border border-white/80 bg-white/70 p-3 sm:w-80">
      <p className="text-sm text-ink">
        Archive <span className="font-semibold">{p.displayName}</span> ({p.mrn})? They disappear from every list, queue, approval and dashboard, and
        their bed is freed. Nothing is deleted, and the archive is recorded in the audit trail.
      </p>
      <ErrorText error={archive.error} />
      <div className="flex gap-2">
        <Button variant="danger" disabled={archive.isPending} onClick={() => archive.mutate({ ref: p.ref, reason: "Test or demo patient" })}>
          {archive.isPending ? "Archiving" : "Archive patient"}
        </Button>
        <Button variant="quiet" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function Row({ p }: { p: PatientRecord }) {
  const canAuthorize = useCan("discharge:authorize");
  const canEdit = useCan("patient:write");
  const canArchive = useCan("user:manage");
  return (
    <li className="grid gap-3 px-4 py-4 md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1.2fr)_auto] md:items-center md:gap-6">
      <div className="min-w-0">
        <p className="truncate font-semibold text-ink">{p.displayName}</p>
        <p className="tnum truncate text-sm text-slate">
          {p.ward}
          {p.bed && `, ${p.bed}`} <span className="text-faint">/ {p.mrn}</span>
        </p>
      </div>
      <div className="min-w-0 text-sm text-slate">
        <p className="truncate">{p.payer}</p>
        <p className="truncate text-xs text-faint">
          {p.attending}, admitted {duration(minutesSince(p.admittedAt))} ago{p.source === "fhir" && ", from FHIR"}
        </p>
      </div>
      <div className="min-w-0 text-xs text-slate">
        {p.departments.map((d) => DEPT_LABEL[d]).join(", ")}
        {!p.consentForCoordination && (
          <p className="mt-1 text-[#8a560c]">
            No consent for coordination recorded. {canEdit && <ConsentToggle p={p} />}
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-center justify-start gap-2 md:justify-end">
        {p.workflow ? (
          <>
            <WorkflowPill status={p.workflow.status} />
            <Link href={`/workflows/${p.workflow.id}`} className="rounded-xl px-3 py-2 text-sm font-semibold text-harbor hover:bg-white/60">
              Open
            </Link>
            <Link
              href={`/workflows/${p.workflow.id}/report`}
              className="inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-semibold text-slate hover:bg-white/60 hover:text-ink"
            >
              <FileText className="size-4" aria-hidden /> Report
            </Link>
          </>
        ) : p.status === "admitted" ? (
          canAuthorize ? (
            <AuthorizeButton p={p} />
          ) : (
            <span className="text-sm text-faint">Waiting for a clinician to authorize discharge</span>
          )
        ) : null}
        {canArchive && p.status !== "archived" && <ArchiveButton p={p} />}
      </div>
    </li>
  );
}

export function Patients() {
  const canAdmit = useCan("patient:write");
  const [status, setStatus] = useState<"admitted" | "discharged">("admitted");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const { data, isPending, error } = usePatients(status, query);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <>
      <PageHeader title="Patients">
        <p>Admitted patients and their discharge status. A clinician authorizes discharge here; CareFlow takes over the administrative work from that moment.</p>
      </PageHeader>

      {adding && (
        <div className="mb-5">
          <AdmitForm onDone={() => setAdding(false)} />
        </div>
      )}

      <Panel className="p-2 sm:p-3">
        <div className="flex flex-wrap items-center justify-between gap-3 px-2 pb-2 pt-1">
          <div role="tablist" aria-label="Patient status" className="flex gap-1 rounded-full bg-ink/[0.05] p-1">
            {(["admitted", "discharged"] as const).map((s) => (
              <button
                key={s}
                role="tab"
                aria-selected={status === s}
                onClick={() => setStatus(s)}
                className={clsx("rounded-full px-3 py-1.5 text-sm font-semibold", status === s ? "bg-white text-ink shadow-sm" : "text-slate hover:text-ink")}
              >
                {s === "admitted" ? "In hospital" : "Discharged"}
              </button>
            ))}
          </div>
          <div className="flex flex-1 flex-wrap justify-end gap-2">
            <div className="relative min-w-0 flex-1 sm:max-w-xs">
              <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
              <label htmlFor="psearch" className="sr-only">
                Search patients
              </label>
              <input id="psearch" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name, MRN or ward" className={clsx(inputClass, "pl-9")} />
            </div>
            {canAdmit && !adding && (
              <Button onClick={() => setAdding(true)}>
                <Plus className="size-4" aria-hidden /> Admit patient
              </Button>
            )}
          </div>
        </div>

        {error ? (
          <p className="p-4 text-sm text-coral">{error.message}</p>
        ) : isPending ? (
          <div className="flex flex-col gap-2 p-2">
            <Skeleton className="h-16" />
            <Skeleton className="h-16" />
          </div>
        ) : data.length === 0 ? (
          <div className="px-4 py-10 text-center">
            <p className="font-semibold text-ink">
              {query ? "No patients match that search." : status === "admitted" ? "No patients in hospital yet." : "No discharged patients yet."}
            </p>
            {status === "admitted" && !query && canAdmit && <p className="mt-1 text-sm text-slate">Admit the first patient to start using CareFlow.</p>}
          </div>
        ) : (
          <ul className="divide-y divide-white/70 border-t border-white/70">
            {data.map((p) => (
              <Row key={p.ref} p={p} />
            ))}
          </ul>
        )}
      </Panel>
    </>
  );
}
