"use client";

import clsx from "clsx";
import { KeyRound, Send } from "lucide-react";
import { useState } from "react";
import { Button, ErrorText, Field, PageHeader, Panel, Skeleton, inputClass } from "./ui";
import { since } from "@/lib/format";
import { useIntegrationKeyMutations, useIntegrationKeys, useIntegrationMessages, useIntegrationStatus, useSendHl7 } from "@/lib/queries";
import { useCan } from "@/lib/session";
import type { IntegrationSystemStatus } from "@/lib/types";

const HEALTH = {
  ok: { label: "Working", tone: "bg-tide-soft text-success" },
  degraded: { label: "Errors", tone: "bg-coral-soft text-coral" },
  idle: { label: "No traffic", tone: "bg-ink/[0.06] text-slate" },
};

const STATUS_TONE: Record<string, string> = {
  accepted: "text-success", sent: "text-success", duplicate: "text-slate", warning: "text-amber-ink",
  error: "text-coral", rejected: "text-coral", failed: "text-coral", skipped: "text-faint",
};

function stamp() {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}`;
}

const SAMPLES: Record<string, (mrn: string) => string> = {
  "ADT^A01 admit (cashless)": (mrn) => [
    `MSH|^~\\&|HIS|HOSPITAL|CAREFLOW|HOSPITAL|${stamp()}||ADT^A01^ADT_A01|A01-${stamp()}|P|2.5`,
    `EVN|A01|${stamp()}`,
    `PID|1||${mrn}^^^HOSPITAL^MR||Sharma^Neha||19880412|F`,
    "PV1|1|I|Ward 5^^7||||D200^Rao^Vikram^^^Dr.|||MED|||||||||V-" + mrn,
    "IN1|1|ICICI01|ICICI|ICICI Lombard|||||||||||CASHLESS",
  ].join("\n"),
  "ORM^O01 lab order": (mrn) => [
    `MSH|^~\\&|HIS|HOSPITAL|CAREFLOW|HOSPITAL|${stamp()}||ORM^O01|ORM-${stamp()}|P|2.5`,
    `PID|1||${mrn}^^^HOSPITAL^MR||Sharma^Neha`,
    "ORC|NW|PL-100",
    "OBR|1|PL-100|LAB-100|CBC^Complete blood count",
  ].join("\n"),
  "ORU^R01 lab result final": (mrn) => [
    `MSH|^~\\&|LIS|HOSPITAL|CAREFLOW|HOSPITAL|${stamp()}||ORU^R01|ORU-${stamp()}|P|2.5`,
    `PID|1||${mrn}^^^HOSPITAL^MR||Sharma^Neha`,
    "OBR|1|PL-100|LAB-100|CBC^Complete blood count" + "|".repeat(21) + "F", // OBR-25 result status F = final
  ].join("\n"),
  "RDE^O11 discharge medicines ordered": (mrn) => [
    `MSH|^~\\&|PHARMACY|HOSPITAL|CAREFLOW|HOSPITAL|${stamp()}||RDE^O11|RDE-${stamp()}|P|2.5`,
    `PID|1||${mrn}^^^HOSPITAL^MR||Sharma^Neha`,
    `ORC|NW|RX-100|||||||${stamp()}||||||||||||||||||||O`,
    "RXE|^^^|PARA500^Paracetamol 500 mg",
  ].join("\n"),
  "ADT^A02 transfer": (mrn) => [
    `MSH|^~\\&|HIS|HOSPITAL|CAREFLOW|HOSPITAL|${stamp()}||ADT^A02^ADT_A02|A02-${stamp()}|P|2.5`,
    `PID|1||${mrn}^^^HOSPITAL^MR||Sharma^Neha`,
    "PV1|1|I|Ward 6^^2",
  ].join("\n"),
  "ADT^A03 discharge (from HIS)": (mrn) => [
    `MSH|^~\\&|HIS|HOSPITAL|CAREFLOW|HOSPITAL|${stamp()}||ADT^A03^ADT_A03|A03-${stamp()}|P|2.5`,
    `PID|1||${mrn}^^^HOSPITAL^MR||Sharma^Neha`,
    "PV1|1|I|Ward 5^^7",
  ].join("\n"),
};

function SystemCard({ s }: { s: IntegrationSystemStatus }) {
  return (
    <li className="glass flex flex-col gap-2 rounded-2xl p-4">
      <div className="flex items-start justify-between gap-2">
        <p className="font-bold text-ink">{s.label}</p>
        <span className={clsx("rounded-full px-2 py-0.5 text-xs font-semibold", HEALTH[s.health].tone)}>{HEALTH[s.health].label}</span>
      </div>
      <dl className="tnum grid grid-cols-3 gap-2 text-sm">
        <div><dt className="text-xs text-faint">In (24h)</dt><dd className="font-semibold text-ink">{s.inbound24h}</dd></div>
        <div><dt className="text-xs text-faint">Out (24h)</dt><dd className="font-semibold text-ink">{s.outbound24h}</dd></div>
        <div><dt className="text-xs text-faint">Errors</dt><dd className={clsx("font-semibold", s.errors24h ? "text-coral" : "text-ink")}>{s.errors24h}</dd></div>
      </dl>
      <p className="text-xs text-slate">
        {s.activeKeys ? `${s.activeKeys} active key${s.activeKeys > 1 ? "s" : ""}` : "No inbound key yet"} ·{" "}
        {s.outboundConfigured ? "outbound notifications on" : "outbound not configured"}
        {s.hl7OutboundConfigured ? " · ADT^A03 on discharge" : ""}
      </p>
      <p className="text-xs text-faint">{s.lastMessageAt ? `Last message ${since(s.lastMessageAt)}` : "No messages yet"}</p>
    </li>
  );
}

function Keys() {
  const { data, isPending } = useIntegrationKeys();
  const { create, revoke } = useIntegrationKeyMutations();
  const [name, setName] = useState("");
  const [system, setSystem] = useState("his");
  const [shown, setShown] = useState<string | null>(null);
  return (
    <Panel className="p-4 sm:p-5">
      <h2 className="text-lg font-bold text-ink">Integration keys</h2>
      <p className="mt-1 text-sm text-slate">
        Each connected system authenticates with its own key in the <code className="rounded bg-ink/[0.06] px-1">X-CareFlow-Key</code> header. A key only works for its
        system. CareFlow stores a hash, so copy the key when it&apos;s shown.
      </p>
      <form
        className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate({ name, system }, { onSuccess: (r) => (setShown(r.key), setName("")) });
        }}
      >
        <Field label="Name" htmlFor="k-name">
          <input id="k-name" value={name} onChange={(e) => setName(e.target.value)} className={inputClass} required minLength={2} placeholder="Mirth channel: ADT feed" />
        </Field>
        <Field label="System" htmlFor="k-sys">
          <select id="k-sys" value={system} onChange={(e) => setSystem(e.target.value)} className={inputClass}>
            <option value="his">Hospital system (HL7 / ADT)</option>
            <option value="tpa">TPA / payer</option>
            <option value="pharmacy">Pharmacy</option>
            <option value="lis">Laboratory</option>
            <option value="any">Any (interface engine)</option>
          </select>
        </Field>
        <Button type="submit" disabled={create.isPending}><KeyRound className="size-4" aria-hidden /> Create key</Button>
      </form>
      <div className="mt-3"><ErrorText error={create.error || revoke.error} /></div>
      {shown && (
        <div role="status" className="mt-3 rounded-xl bg-tide-soft px-3 py-2 text-sm text-ink">
          <p className="font-semibold">New key (shown once):</p>
          <code className="mt-1 block break-all text-xs">{shown}</code>
          <Button variant="quiet" className="mt-1 px-0" onClick={() => setShown(null)}>I&apos;ve copied it</Button>
        </div>
      )}
      {isPending ? <Skeleton className="mt-3 h-16" /> : (
        <ul className="mt-3 divide-y divide-line text-sm">
          {(data ?? []).map((k) => (
            <li key={k.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span className="min-w-0">
                <span className={clsx("font-semibold", k.isActive ? "text-ink" : "text-faint line-through")}>{k.name}</span>{" "}
                <span className="text-xs text-faint">{k.system} · {k.prefix}… · {k.lastUsedAt ? `used ${since(k.lastUsedAt)}` : "never used"}</span>
              </span>
              {k.isActive && <Button variant="danger" className="px-2.5 py-1 text-xs" onClick={() => revoke.mutate(k.id)}>Revoke</Button>}
            </li>
          ))}
          {data?.length === 0 && <li className="py-2 text-slate">No keys yet.</li>}
        </ul>
      )}
    </Panel>
  );
}

function Console() {
  const send = useSendHl7();
  const [mrn, setMrn] = useState("HL7-1001");
  const [text, setText] = useState(SAMPLES["ADT^A01 admit (cashless)"]("HL7-1001"));
  return (
    <Panel className="p-4 sm:p-5">
      <h2 className="text-lg font-bold text-ink">HL7 test console</h2>
      <p className="mt-1 text-sm text-slate">
        Send an HL7 v2 message as if it came from the hospital system, and see CareFlow&apos;s ACK. Pick a sample, or paste a real message. Use a test MRN.
      </p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <Field label="Test MRN" htmlFor="h-mrn">
          <input id="h-mrn" value={mrn} onChange={(e) => setMrn(e.target.value)} className={`${inputClass} w-36`} />
        </Field>
        {Object.keys(SAMPLES).map((k) => (
          <Button key={k} variant="secondary" className="px-2.5 py-1.5 text-xs" onClick={() => setText(SAMPLES[k](mrn.trim() || "HL7-1001"))}>{k}</Button>
        ))}
      </div>
      <label htmlFor="h-text" className="sr-only">HL7 message</label>
      <textarea id="h-text" value={text} onChange={(e) => setText(e.target.value)} rows={7} spellCheck={false}
                className={`${inputClass} mt-3 font-mono text-xs leading-relaxed`} />
      <div className="mt-2 flex items-center gap-3">
        <Button disabled={send.isPending || !text.trim()} onClick={() => send.mutate(text)}><Send className="size-4" aria-hidden /> Send</Button>
        {send.data && (
          <span className={clsx("text-sm font-semibold", send.data.code === "AA" ? "text-success" : "text-coral")}>
            {send.data.code === "AA" ? "Accepted (AA)" : send.data.code === "AR" ? "Rejected (AR)" : "Error (AE)"}
          </span>
        )}
      </div>
      <div className="mt-2"><ErrorText error={send.error} /></div>
      {send.data && <pre className="mt-2 overflow-x-auto rounded-xl bg-ink/[0.05] p-3 text-xs text-ink">{send.data.ack}</pre>}
    </Panel>
  );
}

function MessageLog() {
  const [system, setSystem] = useState("");
  const [direction, setDirection] = useState("");
  const { data, isPending, error } = useIntegrationMessages(system, direction);
  return (
    <Panel className="p-4 sm:p-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-lg font-bold text-ink">Message log</h2>
          <p className="mt-1 text-sm text-slate">Every message to and from connected systems, newest first.</p>
        </div>
        <div className="flex gap-2">
          <label className="sr-only" htmlFor="m-sys">System</label>
          <select id="m-sys" value={system} onChange={(e) => setSystem(e.target.value)} className={`${inputClass} w-36`}>
            <option value="">All systems</option><option value="his">HIS</option><option value="tpa">TPA</option><option value="pharmacy">Pharmacy</option><option value="lis">LIS</option>
          </select>
          <label className="sr-only" htmlFor="m-dir">Direction</label>
          <select id="m-dir" value={direction} onChange={(e) => setDirection(e.target.value)} className={`${inputClass} w-32`}>
            <option value="">Both ways</option><option value="inbound">Inbound</option><option value="outbound">Outbound</option>
          </select>
        </div>
      </div>
      {error ? <p className="mt-3 text-sm text-coral">{error.message}</p> : isPending ? <Skeleton className="mt-3 h-32" /> : data.length === 0 ? (
        <p className="mt-3 text-sm text-slate">No messages yet.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line">
          {data.map((m) => (
            <li key={m.id} className="py-2.5">
              <details>
                <summary className="flex cursor-pointer flex-wrap items-baseline gap-x-3 gap-y-1 text-sm">
                  <span className="tnum w-20 shrink-0 text-xs text-faint">{since(m.at)}</span>
                  <span className="w-16 shrink-0 text-xs font-semibold uppercase text-slate">{m.direction === "inbound" ? "in" : "out"} · {m.system}</span>
                  <span className="font-semibold text-ink">{m.type}</span>
                  <span className={clsx("text-xs font-semibold", STATUS_TONE[m.status] ?? "text-slate")}>{m.status}</span>
                  <span className="min-w-0 flex-1 truncate text-slate">{m.detail}</span>
                </summary>
                <div className="mt-2 flex flex-col gap-1 text-xs text-slate">
                  <p>Control ID {m.controlId || "none"} · {m.protocol} · {m.attempts} attempt{m.attempts > 1 ? "s" : ""}{m.patientRef ? ` · patient ${m.patientRef}` : ""}</p>
                  <p className="whitespace-pre-wrap">{m.detail}</p>
                  {m.raw && <pre className="overflow-x-auto rounded-xl bg-ink/[0.05] p-3 text-ink">{m.raw.replace(/\r/g, "\n")}</pre>}
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

export function Integrations() {
  const { data, isPending, error } = useIntegrationStatus();
  const isAdmin = useCan("user:manage");
  return (
    <>
      <PageHeader title="Integrations">
        <p>
          CareFlow connects to the hospital information system over HL7 v2 (MLLP port {data?.mllp.port ?? 2575} or HTTPS), and to the payer, pharmacy and
          laboratory systems through JSON connectors. Updates from these systems are re-checked by CareFlow&apos;s agents exactly like a staff click.
        </p>
      </PageHeader>
      {error ? <Panel className="p-6 text-sm text-coral">{error.message}</Panel> : isPending ? <Skeleton className="h-40" /> : (
        <div className="flex flex-col gap-5">
          <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
            {data.systems.map((s) => <SystemCard key={s.system} s={s} />)}
          </ul>
          <p className="text-xs text-faint">
            MLLP listener: port {data.mllp.port}
            {data.mllp.allowedHosts.length ? `, accepts ${data.mllp.allowedHosts.join(", ")}` : ", accepts any host (restrict with CAREFLOW_HL7_ALLOWED_HOSTS)"}.
            Outbound webhooks are {data.signing ? "signed (X-CareFlow-Signature, HMAC-SHA256)" : "not signed (set CAREFLOW_OUTBOUND_SECRET)"}.
          </p>
          {isAdmin && (
            <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
              <Keys />
              <Console />
            </div>
          )}
          <MessageLog />
        </div>
      )}
    </>
  );
}
