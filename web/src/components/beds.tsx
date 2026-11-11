"use client";

import clsx from "clsx";
import { Plus } from "lucide-react";
import { useMemo, useState } from "react";
import { Button, ErrorText, Field, PageHeader, Panel, Skeleton, inputClass } from "./ui";
import { since } from "@/lib/format";
import { useBedMutations, useBeds } from "@/lib/queries";
import { useCan } from "@/lib/session";
import type { BedInfo, BedStatus } from "@/lib/types";

const STATUS: Record<BedStatus, { label: string; tone: string }> = {
  available: { label: "Available", tone: "bg-tide-soft text-[#0a6b64]" },
  occupied: { label: "Occupied", tone: "bg-harbor-soft text-harbor" },
  cleaning: { label: "Cleaning", tone: "bg-amber-soft text-[#8a560c]" },
  blocked: { label: "Out of service", tone: "bg-ink/[0.07] text-slate" },
};

function AddBeds({ onDone }: { onDone: () => void }) {
  const { add } = useBedMutations();
  const [ward, setWard] = useState("");
  const [beds, setBeds] = useState("");
  const list = beds.split(/[\s,]+/).map((b) => b.trim()).filter(Boolean);
  return (
    <Panel strong className="mb-5 p-5">
      <h2 className="text-lg font-bold text-ink">Add beds</h2>
      <form
        className="mt-3 grid gap-4 sm:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          add.mutate({ ward, beds: list }, { onSuccess: onDone });
        }}
      >
        <Field label="Ward" htmlFor="b-ward">
          <input id="b-ward" value={ward} onChange={(e) => setWard(e.target.value)} className={inputClass} required placeholder="Ward 3A" />
        </Field>
        <Field label="Bed numbers" htmlFor="b-beds" hint="Separate with commas or spaces, e.g. 1, 2, 3">
          <input id="b-beds" value={beds} onChange={(e) => setBeds(e.target.value)} className={inputClass} required placeholder="1, 2, 3, 4" />
        </Field>
        <div className="flex gap-2">
          <Button type="submit" disabled={add.isPending || !ward.trim() || !list.length}>Add {list.length || ""}</Button>
          <Button variant="secondary" onClick={onDone}>Cancel</Button>
        </div>
      </form>
      <div className="mt-3"><ErrorText error={add.error} /></div>
    </Panel>
  );
}

function BedCard({ b, canManage }: { b: BedInfo; canManage: boolean }) {
  const { setStatus } = useBedMutations();
  const next: { status: BedStatus; label: string }[] =
    b.status === "cleaning" ? [{ status: "available", label: "Mark clean" }]
    : b.status === "available" ? [{ status: "blocked", label: "Take out of service" }]
    : b.status === "blocked" ? [{ status: "available", label: "Back in service" }]
    : [];
  return (
    <li className="glass flex flex-col gap-2 rounded-2xl p-3">
      <div className="flex items-start justify-between gap-2">
        <p className="tnum text-base font-bold text-ink">{b.bed}</p>
        <span className={clsx("rounded-full px-2 py-0.5 text-xs font-semibold", STATUS[b.status].tone)}>{STATUS[b.status].label}</span>
      </div>
      <p className="min-h-5 truncate text-sm text-slate">{b.patientName ?? (b.status === "available" ? "Ready for a patient" : "")}</p>
      <p className="text-xs text-faint">Updated {since(b.updatedAt)}{b.updatedBy ? ` by ${b.updatedBy}` : ""}</p>
      {canManage && next.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {next.map((n) => (
            <Button key={n.status} variant="secondary" className="px-2.5 py-1.5 text-xs" disabled={setStatus.isPending}
                    onClick={() => setStatus.mutate({ id: b.id, status: n.status })}>
              {n.label}
            </Button>
          ))}
        </div>
      )}
      {setStatus.error && <ErrorText error={setStatus.error} />}
    </li>
  );
}

export function Beds() {
  const { data, isPending, error } = useBeds();
  const canManage = useCan("bed:manage");
  const [adding, setAdding] = useState(false);
  const [filter, setFilter] = useState<BedStatus | "all">("all");
  const wards = useMemo(() => {
    const m = new Map<string, BedInfo[]>();
    for (const b of data?.beds ?? []) if (filter === "all" || b.status === filter) m.set(b.ward, [...(m.get(b.ward) ?? []), b]);
    return [...m.entries()];
  }, [data, filter]);

  return (
    <>
      <PageHeader title="Beds">
        <p>
          Beds fill when a patient is admitted (by staff or from the hospital system) and go to cleaning automatically when their discharge is closed.
          Housekeeping marks them clean here. Changes are sent to the hospital system when one is connected.
        </p>
      </PageHeader>
      {canManage && (adding ? <AddBeds onDone={() => setAdding(false)} /> : (
        <div className="mb-4"><Button onClick={() => setAdding(true)}><Plus className="size-4" aria-hidden /> Add beds</Button></div>
      ))}
      {error ? (
        <Panel className="p-6 text-sm text-coral">{error.message}</Panel>
      ) : isPending ? (
        <Skeleton className="h-48" />
      ) : (
        <>
          <div role="group" aria-label="Filter beds" className="mb-4 flex w-fit flex-wrap gap-1 rounded-full bg-ink/[0.05] p-1">
            {(["all", "available", "occupied", "cleaning", "blocked"] as const).map((k) => (
              <button key={k} type="button" aria-pressed={filter === k} onClick={() => setFilter(k)}
                      className={clsx("tnum rounded-full px-3 py-1.5 text-sm font-semibold", filter === k ? "bg-white text-ink shadow-sm" : "text-slate hover:text-ink")}>
                {k === "all" ? `All ${data.summary.total}` : `${STATUS[k].label} ${data.summary[k]}`}
              </button>
            ))}
          </div>
          {wards.length === 0 ? (
            <Panel className="p-6 text-sm text-slate">{data.summary.total ? "No beds match this filter." : "No beds yet. They appear as patients are admitted with a ward and bed, or add them above."}</Panel>
          ) : (
            <div className="flex flex-col gap-5">
              {wards.map(([ward, beds]) => (
                <Panel key={ward} className="p-4 sm:p-5">
                  <h2 className="text-lg font-bold text-ink">{ward}</h2>
                  <ul className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(11rem,1fr))] gap-3">
                    {beds.map((b) => <BedCard key={b.id} b={b} canManage={canManage} />)}
                  </ul>
                </Panel>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}
