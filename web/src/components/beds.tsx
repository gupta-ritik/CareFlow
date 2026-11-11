"use client";

import clsx from "clsx";
import { BedDouble, BedSingle, Ban, Plus, Search, Sparkles, UserRound } from "lucide-react";
import { useMemo, useState } from "react";
import { Badge, Button, Dialog, Drawer, EmptyState, ErrorText, Field, PageHeader, Panel, Skeleton, StatCard, Tabs, inputClass, selectClass, toast, type Tone } from "./ui";
import { clock, since } from "@/lib/format";
import { useBedMutations, useBeds } from "@/lib/queries";
import { useCan } from "@/lib/session";
import type { BedInfo, BedStatus } from "@/lib/types";

const STATUS: Record<BedStatus, { label: string; tone: Tone; bar: string; tile: string }> = {
  available: { label: "Available", tone: "success", bar: "bg-success", tile: "border-l-success" },
  occupied: { label: "Occupied", tone: "info", bar: "bg-harbor", tile: "border-l-harbor" },
  cleaning: { label: "Cleaning", tone: "warning", bar: "bg-amber", tile: "border-l-amber" },
  blocked: { label: "Out of service", tone: "neutral", bar: "bg-disabled", tile: "border-l-disabled" },
};
const ORDER: BedStatus[] = ["occupied", "cleaning", "available", "blocked"];
const natural = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });

/** What housekeeping or the coordinator can do next with a bed (the same transitions the API allows). */
function nextSteps(b: BedInfo): { status: BedStatus; label: string; variant: "good" | "secondary" }[] {
  if (b.status === "cleaning") return [{ status: "available", label: "Mark clean", variant: "good" }];
  if (b.status === "available") return [{ status: "blocked", label: "Take out of service", variant: "secondary" }];
  if (b.status === "blocked") return [{ status: "available", label: "Back in service", variant: "good" }];
  return [];
}

function AddBeds({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { add } = useBedMutations();
  const [ward, setWard] = useState("");
  const [beds, setBeds] = useState("");
  const list = beds
    .split(/[\s,]+/)
    .map((b) => b.trim())
    .filter(Boolean);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add beds"
      description="Add a ward's beds so they appear on the board before patients are admitted to them."
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button
            loading={add.isPending}
            disabled={!ward.trim() || !list.length}
            onClick={() =>
              add.mutate(
                { ward, beds: list },
                {
                  onSuccess: () => {
                    toast.success(`Added ${list.length} bed${list.length === 1 ? "" : "s"} to ${ward}.`);
                    setBeds("");
                    onClose();
                  },
                },
              )
            }
          >
            Add {list.length || ""} bed{list.length === 1 ? "" : "s"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Ward" htmlFor="b-ward" required>
          <input id="b-ward" value={ward} onChange={(e) => setWard(e.target.value)} className={inputClass} placeholder="Ward 3A" />
        </Field>
        <Field label="Bed numbers" htmlFor="b-beds" hint="Separate with commas or spaces, e.g. 1, 2, 3" required>
          <input id="b-beds" value={beds} onChange={(e) => setBeds(e.target.value)} className={inputClass} placeholder="1, 2, 3, 4" />
        </Field>
        <ErrorText error={add.error} />
      </div>
    </Dialog>
  );
}

function BedDetails({ bed, onClose, canManage }: { bed: BedInfo | null; onClose: () => void; canManage: boolean }) {
  const { setStatus } = useBedMutations();
  if (!bed) return null;
  const steps = nextSteps(bed);
  return (
    <Drawer
      open
      onClose={onClose}
      title={`${bed.ward} · Bed ${bed.bed}`}
      subtitle={<Badge tone={STATUS[bed.status].tone}>{STATUS[bed.status].label}</Badge>}
      footer={
        canManage && steps.length > 0 ? (
          steps.map((s) => (
            <Button
              key={s.status}
              variant={s.variant}
              loading={setStatus.isPending}
              onClick={() =>
                setStatus.mutate(
                  { id: bed.id, status: s.status },
                  {
                    onSuccess: () => {
                      toast.success(`${bed.ward} bed ${bed.bed}: ${STATUS[s.status].label.toLowerCase()}.`);
                      onClose();
                    },
                  },
                )
              }
            >
              {s.label}
            </Button>
          ))
        ) : undefined
      }
    >
      <dl className="grid grid-cols-2 gap-x-4 gap-y-4 text-sm">
        <div>
          <dt className="text-xs text-faint">Ward</dt>
          <dd className="mt-0.5 font-medium text-ink">{bed.ward}</dd>
        </div>
        <div>
          <dt className="text-xs text-faint">Bed</dt>
          <dd className="mt-0.5 font-medium text-ink">{bed.bed}</dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-faint">Patient</dt>
          <dd className="mt-0.5 font-medium text-ink">
            {bed.patientName ?? (bed.status === "occupied" ? "Hidden for your role" : "None")}
            {bed.patientStatus && <span className="ml-2 text-xs font-normal text-slate">({bed.patientStatus})</span>}
          </dd>
        </div>
        <div className="col-span-2">
          <dt className="text-xs text-faint">Last updated</dt>
          <dd className="mt-0.5 text-ink">
            {since(bed.updatedAt)} ({clock(bed.updatedAt)}){bed.updatedBy ? ` by ${bed.updatedBy}` : ""}
          </dd>
        </div>
      </dl>
      <p className="mt-6 rounded-xl bg-surface-2 px-3.5 py-3 text-[13px] leading-relaxed text-slate ring-1 ring-line">
        Beds fill when a patient is admitted and move to cleaning automatically when their discharge is closed. Assigning a patient to a bed happens on the
        Patients page (ward and bed).
      </p>
      {!canManage && steps.length > 0 && <p className="mt-3 text-xs text-faint">Your role can view beds but not change them.</p>}
      <div className="mt-3">
        <ErrorText error={setStatus.error} />
      </div>
    </Drawer>
  );
}

export function Beds() {
  const { data, isPending, error } = useBeds();
  const canManage = useCan("bed:manage");
  const [adding, setAdding] = useState(false);
  const [filter, setFilter] = useState<BedStatus | "all">("all");
  const [ward, setWard] = useState("");
  const [q, setQ] = useState("");
  const [openId, setOpenId] = useState<string | null>(null);

  const allWards = useMemo(() => [...new Set((data?.beds ?? []).map((b) => b.ward))].sort(natural), [data]);
  const wards = useMemo(() => {
    const term = q.trim().toLowerCase();
    const m = new Map<string, BedInfo[]>();
    for (const b of data?.beds ?? []) {
      if (filter !== "all" && b.status !== filter) continue;
      if (ward && b.ward !== ward) continue;
      if (term && ![b.bed, b.ward, b.patientName ?? ""].some((s) => s.toLowerCase().includes(term))) continue;
      m.set(b.ward, [...(m.get(b.ward) ?? []), b]);
    }
    return [...m.entries()].sort(([a], [b]) => natural(a, b)).map(([w, beds]) => [w, beds.sort((x, y) => natural(x.bed, y.bed))] as const);
  }, [data, filter, ward, q]);
  const wardTotals = useMemo(() => {
    const t = new Map<string, Record<BedStatus, number>>();
    for (const b of data?.beds ?? []) {
      const r = t.get(b.ward) ?? { available: 0, occupied: 0, cleaning: 0, blocked: 0 };
      r[b.status] += 1;
      t.set(b.ward, r);
    }
    return t;
  }, [data]);
  const open = data?.beds.find((b) => b.id === openId) ?? null;
  const s = data?.summary;

  return (
    <>
      <PageHeader
        title="Beds"
        actions={
          canManage && (
            <Button onClick={() => setAdding(true)}>
              <Plus className="size-4" aria-hidden /> Add beds
            </Button>
          )
        }
      >
        Beds fill when a patient is admitted and go to cleaning when their discharge is closed. Housekeeping marks them clean here; changes reach the
        hospital system when one is connected.
      </PageHeader>

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        <StatCard
          label="Total beds"
          value={s ? s.total : "–"}
          icon={BedDouble}
          tone="brand"
          hint={s?.occupancy != null ? `${Math.round(s.occupancy * 100)}% occupied` : undefined}
        />
        <StatCard label="Available" value={s ? s.available : "–"} icon={BedSingle} tone="success" help="Clean and ready for a patient." />
        <StatCard label="Occupied" value={s ? s.occupied : "–"} icon={UserRound} tone="info" />
        <StatCard label="Cleaning" value={s ? s.cleaning : "–"} icon={Sparkles} tone="warning" help="Patient has left; waiting for housekeeping." />
        <StatCard label="Out of service" value={s ? s.blocked : "–"} icon={Ban} help="Unavailable, e.g. for maintenance." className="col-span-2 md:col-span-1" />
      </div>

      {error ? (
        <Panel className="p-6">
          <ErrorText error={error} />
        </Panel>
      ) : isPending ? (
        <Skeleton className="h-64" />
      ) : (
        <>
          <Panel className="mb-4 flex flex-col gap-3 p-3 sm:p-4 lg:flex-row lg:items-center lg:justify-between">
            <Tabs<BedStatus | "all">
              wrap
              label="Filter beds by status"
              value={filter}
              onChange={setFilter}
              options={[
                { value: "all", label: "All", count: data.summary.total },
                ...ORDER.map((k) => ({ value: k, label: STATUS[k].label, count: data.summary[k] })),
              ]}
            />
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative sm:w-56">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-faint" aria-hidden />
                <input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Bed or patient" aria-label="Search beds" className={clsx(inputClass, "pl-9")} />
              </div>
              <select value={ward} onChange={(e) => setWard(e.target.value)} aria-label="Ward" className={clsx(selectClass, "sm:w-44")}>
                <option value="">All wards</option>
                {allWards.map((w) => (
                  <option key={w} value={w}>
                    {w}
                  </option>
                ))}
              </select>
            </div>
          </Panel>

          {wards.length === 0 ? (
            <Panel>
              <EmptyState icon={BedDouble} title={data.summary.total ? "No beds match these filters" : "No beds yet"}>
                {data.summary.total ? "Try another status or ward." : "Beds appear as patients are admitted with a ward and bed, or add them with Add beds."}
              </EmptyState>
            </Panel>
          ) : (
            <div className="flex flex-col gap-4">
              {wards.map(([w, beds]) => {
                const t = wardTotals.get(w)!;
                const total = ORDER.reduce((n, k) => n + t[k], 0);
                return (
                  <Panel key={w} className="p-4 sm:p-5">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <h2 className="text-[15px] font-semibold text-ink">{w}</h2>
                      <p className="tnum text-xs text-slate">
                        {t.occupied} occupied · {t.available} available
                        {t.cleaning ? ` · ${t.cleaning} cleaning` : ""}
                        {t.blocked ? ` · ${t.blocked} out of service` : ""} · {total} beds
                      </p>
                    </div>
                    <div className="mt-2 flex h-1.5 overflow-hidden rounded-full bg-line" aria-hidden>
                      {ORDER.map((k) => (t[k] ? <span key={k} className={STATUS[k].bar} style={{ width: `${(t[k] / total) * 100}%` }} /> : null))}
                    </div>
                    <ul className="mt-4 grid grid-cols-[repeat(auto-fill,minmax(7.5rem,1fr))] gap-2">
                      {beds.map((b) => (
                        <li key={b.id}>
                          <button
                            type="button"
                            onClick={() => setOpenId(b.id)}
                            className={clsx(
                              "flex h-full w-full flex-col items-start gap-1 rounded-[10px] border border-l-[3px] border-line bg-surface px-3 py-2 text-left transition-colors hover:border-line-strong hover:bg-surface-2",
                              STATUS[b.status].tile,
                            )}
                            aria-label={`${w} bed ${b.bed}, ${STATUS[b.status].label}${b.patientName ? `, ${b.patientName}` : ""}`}
                          >
                            <span className="tnum text-sm font-semibold text-ink">Bed {b.bed}</span>
                            <span className="w-full truncate text-xs text-slate">{b.patientName ?? STATUS[b.status].label}</span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </Panel>
                );
              })}
            </div>
          )}
        </>
      )}

      <AddBeds open={adding} onClose={() => setAdding(false)} />
      <BedDetails key={openId ?? "none"} bed={open} onClose={() => setOpenId(null)} canManage={canManage} />
    </>
  );
}
