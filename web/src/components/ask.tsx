"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, ChevronRight, History, Loader2, MessageSquarePlus, Send, Table2, ThumbsDown, ThumbsUp, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import {
  askQuestion, EXAMPLE_QUESTIONS, getAskSession, listAskSessions, sendAskFeedback, turnToAnswer,
  type AskAnswer, type AskChart, type AskResult, type AskStat,
} from "@/lib/ask";
import { useCan } from "@/lib/session";
import { Button, EvidenceRef, PageHeader, Panel, inputClass } from "./ui";

const INK = "#0f2236";
const SLATE = "#56677a";
const GRID = "rgba(15,34,54,0.08)";
const HARBOR = "#2a63c9";
const TIDE = "#0e8f86";

const DAY = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" } as Record<string, string>;
const DIM_LABEL: Record<string, string> = {
  day: "Day", weekday: "Weekday", hour: "Hour", ward: "Ward", department: "Department", payer_type: "Payer type", tpa: "TPA",
  blocker_kind: "Blocker kind", approver_role: "Approver", document: "Document", mode: "Mode", agent: "Agent", risk: "Risk", side: "",
};

function keyLabel(dim: string, v: string) {
  if (dim === "weekday") return DAY[v] ?? v;
  if (dim === "hour") return `${v.padStart(2, "0")}:00`;
  if (dim === "ward") return `Ward ${v}`;
  if (dim === "day") return v.slice(5);
  return v.replace(/_/g, " ");
}

function statValue(s: AskStat | undefined, y: string): number | null {
  if (!s || s.suppressed) return null;
  const v = (s as unknown as Record<string, unknown>)[y];
  return typeof v === "number" ? v : null;
}

function fmtStat(r: AskResult, s: AskStat | undefined) {
  if (!s) return "n/a";
  if (s.suppressed) return "fewer than 5";
  if (r.kind === "time") return s.median === null || s.median === undefined ? "n/a" : `${s.median} min (median), p90 ${s.p90} min`;
  if (r.kind === "rate") return s.pct === null || s.pct === undefined ? "n/a" : `${s.pct}% (${s.hits} of ${s.n})`;
  return String(s.n);
}

function chartRows(chart: AskChart, r: AskResult) {
  if (chart.x === "side") {
    const c = r.params.compare;
    return [
      { name: c?.focus_label || "Focus", value: statValue(r.focus, chart.y), n: r.focus?.n },
      { name: c?.baseline_label || "Baseline", value: statValue(r.baseline, chart.y), n: r.baseline?.n },
    ];
  }
  return r.groups.map((g) => ({ name: keyLabel(chart.x, g.key[chart.x] ?? ""), value: statValue(g, chart.y), n: g.n }));
}

// ---- pieces -----------------------------------------------------------------------------------------------------------
function AnswerChart({ chart, results }: { chart: AskChart; results: AskResult[] }) {
  const [table, setTable] = useState(false);
  const r = results.find((x) => x.id === chart.result_id);
  if (!r) return null;
  const rows = chartRows(chart, r);
  const yLabel = chart.unit === "%" ? "%" : chart.unit;
  const caption = `${chart.title.charAt(0).toUpperCase()}${chart.title.slice(1)} (${chart.y === "median" ? "median, " : ""}${yLabel}) · ${r.id}`;
  return (
    <figure className="mt-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <figcaption className="text-sm font-semibold text-ink">{caption}</figcaption>
        <Button variant="quiet" onClick={() => setTable((t) => !t)} aria-pressed={table} className="!px-2 !py-1 text-xs">
          <Table2 className="size-3.5" aria-hidden /> {table ? "Show chart" : "Show as table"}
        </Button>
      </div>
      {table ? (
        <div className="overflow-x-auto">
          <table className="tnum w-full text-left text-sm">
            <thead className="text-xs text-slate">
              <tr><th className="py-1 pr-3 font-semibold">{DIM_LABEL[chart.x] || "Group"}</th><th className="py-1 pr-3 font-semibold">Value ({yLabel})</th><th className="py-1 font-semibold">Cases</th></tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.name} className="border-t border-ink/[0.06]">
                  <td className="py-1 pr-3">{row.name}</td>
                  <td className="py-1 pr-3">{row.value ?? "fewer than 5"}</td>
                  <td className="py-1">{String(row.n)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="h-56 w-full" role="img" aria-label={`${caption}. Use "Show as table" to read the values.`}>
          <ResponsiveContainer width="100%" height="100%">
            {chart.type === "line" ? (
              <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" tick={{ fill: SLATE, fontSize: 11 }} tickLine={false} axisLine={{ stroke: GRID }} minTickGap={16} />
                <YAxis tick={{ fill: SLATE, fontSize: 11 }} tickLine={false} axisLine={false} width={44}
                  label={{ value: yLabel, angle: -90, position: "insideLeft", fill: SLATE, fontSize: 11 }} />
                <Tooltip contentStyle={{ borderRadius: 12, border: "none", color: INK }} formatter={(v) => [`${v} ${yLabel}`, chart.y]} />
                <Line type="monotone" dataKey="value" stroke={HARBOR} strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
              </LineChart>
            ) : (
              <BarChart data={rows} margin={{ top: 8, right: 12, bottom: 4, left: 4 }}>
                <CartesianGrid stroke={GRID} vertical={false} />
                <XAxis dataKey="name" tick={{ fill: SLATE, fontSize: 11 }} tickLine={false} axisLine={{ stroke: GRID }} interval={0} />
                <YAxis tick={{ fill: SLATE, fontSize: 11 }} tickLine={false} axisLine={false} width={44}
                  label={{ value: yLabel, angle: -90, position: "insideLeft", fill: SLATE, fontSize: 11 }} />
                <Tooltip contentStyle={{ borderRadius: 12, border: "none", color: INK }} formatter={(v) => [`${v} ${yLabel}`, chart.y]} />
                <Bar dataKey="value" fill={TIDE} radius={[6, 6, 0, 0]} isAnimationActive={false} />
              </BarChart>
            )}
          </ResponsiveContainer>
        </div>
      )}
    </figure>
  );
}

function NumbersDrawer({ answer, onClose }: { answer: AskAnswer; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const cited = (id: string) => answer.sentences.filter((s) => s.claims.some((c) => c.result_id === id)).length;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-ink/20" onClick={onClose}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby={titleId} onClick={(e) => e.stopPropagation()}
        className="glass-strong h-full w-full max-w-xl overflow-y-auto p-5 outline-none sm:rounded-l-[20px]">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id={titleId} className="text-lg font-bold text-ink">The numbers behind this answer</h2>
            <p className="text-sm text-slate">Each result is an approved measure run on your hospital&apos;s totals. Groups with fewer than 5 patients are hidden.</p>
          </div>
          <Button variant="quiet" onClick={onClose} aria-label="Close"><X className="size-4" aria-hidden /></Button>
        </div>
        <div className="flex flex-col gap-4">
          {answer.results.map((r) => (
            <Panel key={r.id} as="article" className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <EvidenceRef id={r.id} />
                <h3 className="font-semibold text-ink">{r.label}</h3>
                <span className="text-xs text-faint">{cited(r.id)} sentence(s) cite this</span>
              </div>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm">
                <dt className="text-slate">Period</dt><dd>{r.params.period.label || `${r.params.period.start} to ${r.params.period.end}`}</dd>
                {Object.keys(r.params.filters).length > 0 && (<><dt className="text-slate">Filters</dt>
                  <dd>{Object.entries(r.params.filters).map(([k, v]) => `${DIM_LABEL[k] ?? k}: ${v.map((x) => keyLabel(k, x)).join(", ")}`).join("; ")}</dd></>)}
                {r.params.group_by.length > 0 && (<><dt className="text-slate">Split by</dt><dd>{r.params.group_by.map((g) => DIM_LABEL[g] ?? g).join(", ")}</dd></>)}
                {r.params.compare && (<><dt className="text-slate">Compared</dt><dd>{r.params.compare.focus_label} against {r.params.compare.baseline_label}</dd></>)}
                <dt className="text-slate">All</dt><dd className="tnum">{fmtStat(r, r.total)} · {String(r.total.n)} {r.noun}</dd>
                {r.focus && (<><dt className="text-slate">{r.params.compare?.focus_label || "Focus"}</dt><dd className="tnum">{fmtStat(r, r.focus)} · {String(r.focus.n)} {r.noun}</dd></>)}
                {r.baseline && (<><dt className="text-slate">{r.params.compare?.baseline_label || "Baseline"}</dt><dd className="tnum">{fmtStat(r, r.baseline)} · {String(r.baseline.n)} {r.noun}</dd></>)}
              </dl>
              {r.groups.length > 0 && (
                <div className="mt-3 max-h-64 overflow-auto">
                  <table className="tnum w-full text-left text-sm">
                    <thead className="sticky top-0 bg-white/80 text-xs text-slate">
                      <tr><th className="py-1 pr-2 font-semibold">{r.params.group_by.map((g) => DIM_LABEL[g] ?? g).join(" / ")}</th><th className="py-1 pr-2 font-semibold">Value</th><th className="py-1 font-semibold">Cases</th></tr>
                    </thead>
                    <tbody>
                      {r.groups.map((g) => (
                        <tr key={JSON.stringify(g.key)} className="border-t border-ink/[0.06]">
                          <td className="py-1 pr-2">{Object.entries(g.key).map(([k, v]) => keyLabel(k, v)).join(" / ")}</td>
                          <td className="py-1 pr-2">{fmtStat(r, g)}</td>
                          <td className="py-1">{String(g.n)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          ))}
          {answer.results.length === 0 && <p className="text-sm text-slate">No data was read for this answer.</p>}
        </div>
      </div>
    </div>
  );
}

function Feedback({ turnId }: { turnId: string }) {
  const [rating, setRating] = useState<1 | -1 | null>(null);
  const [comment, setComment] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const id = useId();
  async function submit(r: 1 | -1, withComment = false) {
    setRating(r);
    setError("");
    try {
      await sendAskFeedback(turnId, r, withComment ? comment : "");
      if (withComment) setSent(true);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="mt-4 flex flex-wrap items-center gap-2 text-sm text-slate">
      <span>Was this useful?</span>
      <Button variant="quiet" aria-pressed={rating === 1} aria-label="Yes, useful" onClick={() => submit(1)} className={clsx("!px-2", rating === 1 && "bg-tide-soft text-[#0a6b64]")}>
        <ThumbsUp className="size-4" aria-hidden />
      </Button>
      <Button variant="quiet" aria-pressed={rating === -1} aria-label="No, not useful" onClick={() => submit(-1)} className={clsx("!px-2", rating === -1 && "bg-coral-soft text-coral")}>
        <ThumbsDown className="size-4" aria-hidden />
      </Button>
      {rating !== null && !sent && (
        <form className="flex min-w-0 flex-1 gap-2" onSubmit={(e) => { e.preventDefault(); submit(rating, true); }}>
          <label htmlFor={id} className="sr-only">Comment (optional)</label>
          <input id={id} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} placeholder="What was right or wrong? (optional)" className={clsx(inputClass, "!py-1.5")} />
          <Button type="submit" variant="secondary" className="!py-1.5">Send</Button>
        </form>
      )}
      {sent && <span className="text-[#0a6b64]">Thanks, saved.</span>}
      {error && <span role="alert" className="text-coral">{error}</span>}
    </div>
  );
}

const STATUS_NOTE: Record<string, { title: string; tone: string }> = {
  refused: { title: "I can't answer that", tone: "bg-coral-soft/70 text-coral" },
  clarify: { title: "One question first", tone: "bg-harbor-soft text-harbor" },
  no_data: { title: "The data can't answer this", tone: "bg-amber-soft text-[#8a560c]" },
  error: { title: "Something went wrong", tone: "bg-coral-soft/70 text-coral" },
};

function AnswerView({ a, onAsk, compact }: { a: AskAnswer; onAsk: (q: string) => void; compact?: boolean }) {
  const [drawer, setDrawer] = useState(false);
  const note = STATUS_NOTE[a.status];
  return (
    <article className="flex flex-col gap-3">
      <p className="text-sm font-semibold text-slate">You asked: <span className="text-ink">{a.question}</span></p>
      {note ? (
        <div className={clsx("rounded-xl px-3.5 py-3 text-sm", note.tone)}>
          <p className="font-semibold">{note.title}</p>
          <p className="mt-1 leading-relaxed">{a.message || a.answer}</p>
          {a.status === "clarify" && <p className="mt-1 text-xs opacity-80">Type your reply in the box below; I&apos;ll add it to your question.</p>}
        </div>
      ) : (
        <>
          {a.partial && (
            <p className="w-fit rounded-full bg-amber-soft px-2.5 py-1 text-xs font-semibold text-[#8a560c]">Partial answer: the time limit was reached</p>
          )}
          <div className="flex flex-col gap-1.5 text-[0.98rem] leading-relaxed text-ink">
            {a.sentences.map((s, i) => (
              <p key={i}>
                {s.text}{" "}
                {[...new Set(s.claims.map((c) => c.result_id))].map((id) => (
                  <button key={id} type="button" onClick={() => setDrawer(true)} className="align-middle" aria-label={`See result ${id}`}><EvidenceRef id={id} /></button>
                ))}
              </p>
            ))}
          </div>
          {a.basedOn && <p className="text-xs leading-relaxed text-slate">{a.basedOn}</p>}
          {a.chart && <AnswerChart chart={a.chart} results={a.results} />}
        </>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {a.results.length > 0 && (
          <Button variant="secondary" onClick={() => setDrawer(true)}><Table2 className="size-4" aria-hidden /> See the numbers</Button>
        )}
        {a.status === "answered" && <span className="text-xs text-faint">{a.mode === "llm" ? "Written by the LLM, every number checked" : "Rule-based answer (no LLM)"}{a.cached ? " · from the last few minutes" : ""}</span>}
      </div>
      {a.followUps.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-[0.06em] text-faint">{a.status === "answered" ? "Ask next" : "You could ask"}</p>
          <Chips items={a.followUps} onPick={onAsk} />
        </div>
      )}
      {a.turnId && a.status === "answered" && !compact && <Feedback key={a.turnId} turnId={a.turnId} />}
      {drawer && <NumbersDrawer answer={a} onClose={() => setDrawer(false)} />}
    </article>
  );
}

function Chips({ items, onPick, disabled }: { items: string[]; onPick: (q: string) => void; disabled?: boolean }) {
  return (
    <ul className="flex flex-wrap gap-2">
      {items.map((q) => (
        <li key={q}>
          <button type="button" disabled={disabled} onClick={() => onPick(q)}
            className="rounded-full border border-white/80 bg-white/60 px-3 py-1.5 text-left text-sm text-ink hover:bg-white disabled:opacity-50">
            {q}
          </button>
        </li>
      ))}
    </ul>
  );
}

function ProgressList({ steps, working }: { steps: string[]; working: boolean }) {
  if (!steps.length) return null;
  return (
    <ol className="flex flex-col gap-1.5 text-sm" aria-label="Progress">
      {steps.map((s, i) => {
        const current = working && i === steps.length - 1;
        return (
          <li key={`${i}-${s}`} className={clsx("flex items-center gap-2", current ? "font-semibold text-ink" : "text-slate")}>
            {current ? <Loader2 className="size-4 text-harbor motion-safe:animate-spin" aria-hidden /> : <Check className="size-4 text-tide" aria-hidden />}
            {s}
          </li>
        );
      })}
    </ol>
  );
}

// ---- the conversation (shared by the dashboard panel and the full page) -----------------------------------------------
function useAsk(onDone?: () => void) {
  const [answers, setAnswers] = useState<AskAnswer[]>([]);
  const [steps, setSteps] = useState<string[]>([]);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  async function ask(question: string) {
    const q = question.trim();
    if (!q || working) return;
    const last = answers[answers.length - 1];
    const full = last?.status === "clarify" ? `${last.question} ${q}` : q;  // a reply to a clarifying question completes it
    abort.current?.abort();
    abort.current = new AbortController();
    setWorking(true);
    setError("");
    setSteps([]);
    try {
      const a = await askQuestion(full, sessionId, (p) => setSteps((s) => [...s, p.message]), abort.current.signal);
      setAnswers((xs) => [...xs, a]);
      if (a.sessionId) setSessionId(a.sessionId);
      onDone?.();
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      setWorking(false);
    }
  }

  function load(sid: string, items: AskAnswer[]) {
    abort.current?.abort();
    setSessionId(sid);
    setAnswers(items);
    setSteps([]);
    setError("");
  }

  function reset() {
    abort.current?.abort();
    setSessionId(null);
    setAnswers([]);
    setSteps([]);
    setError("");
  }

  return { answers, steps, working, error, ask, load, reset, sessionId };
}

function Conversation({ state, compact }: { state: ReturnType<typeof useAsk>; compact?: boolean }) {
  const [text, setText] = useState("");
  const inputId = useId();
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const { answers, steps, working, error } = state;
  const shown = compact ? answers.slice(-1) : answers;
  const last = answers[answers.length - 1];

  useEffect(() => {
    if (last?.status === "clarify") inputRef.current?.focus();
  }, [last]);

  function submit(e?: FormEvent) {
    e?.preventDefault();
    const q = text;
    setText("");
    state.ask(q);
  }

  return (
    <div className="flex flex-col gap-4">
      <form onSubmit={submit} className="flex flex-col gap-2">
        <label htmlFor={inputId} className="text-sm font-semibold text-ink">
          {last?.status === "clarify" ? "Your reply" : "Ask about discharge operations"}
        </label>
        <div className="flex items-end gap-2">
          <textarea id={inputId} ref={inputRef} rows={compact ? 1 : 2} value={text} maxLength={500} disabled={working}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }}
            placeholder={last?.status === "clarify" ? "For example: last Monday" : "For example: Why were discharges slow last Monday?"}
            className={clsx(inputClass, "resize-none")} />
          <Button type="submit" disabled={working || text.trim().length < 3} aria-label="Ask" className="shrink-0 !py-2.5">
            {working ? <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden /> : <Send className="size-4" aria-hidden />}
            <span className="hidden sm:inline">Ask</span>
          </Button>
        </div>
        <p className="text-xs text-faint">Answers use your hospital&apos;s totals only. Ask CareFlow never shows patient names and can&apos;t change data.</p>
      </form>

      {answers.length === 0 && !working && (
        <div>
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-[0.06em] text-faint">Try one of these</p>
          <Chips items={EXAMPLE_QUESTIONS} onPick={state.ask} disabled={working} />
        </div>
      )}

      <div aria-live="polite" aria-atomic="false" className="flex flex-col gap-5">
        {shown.map((a, i) => (
          <div key={a.turnId ?? i} className={clsx(i > 0 && "border-t border-ink/[0.07] pt-5")}>
            <AnswerView a={a} onAsk={state.ask} compact={compact} />
          </div>
        ))}
        {working && <ProgressList steps={steps.length ? steps : ["Sending the question…"]} working />}
        {error && <p role="alert" className="rounded-xl bg-coral-soft/80 px-3 py-2 text-sm text-coral">{error}</p>}
      </div>
    </div>
  );
}

// ---- exports ----------------------------------------------------------------------------------------------------------
/** The compact panel on the Director dashboard. */
export function AskPanel() {
  const allowed = useCan("ask:use");
  const state = useAsk();
  if (!allowed) return null;
  return (
    <Panel strong className="mb-6 p-5">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-lg font-bold tracking-[-0.01em] text-ink">Ask CareFlow</h2>
        <Link href="/ask" className="inline-flex items-center gap-1 text-sm font-semibold text-harbor hover:underline">
          Open full page <ChevronRight className="size-4" aria-hidden />
        </Link>
      </div>
      <Conversation state={state} compact />
    </Panel>
  );
}

/** The full /ask page with history. */
export function AskPage() {
  const allowed = useCan("ask:use");
  const qc = useQueryClient();
  const state = useAsk(() => qc.invalidateQueries({ queryKey: ["ask-sessions"] }));
  const sessions = useQuery({ queryKey: ["ask-sessions"], queryFn: listAskSessions, enabled: allowed });
  const [opening, setOpening] = useState<string | null>(null);

  if (!allowed)
    return (
      <>
        <PageHeader title="Ask CareFlow" />
        <Panel className="p-6 text-sm text-slate">Ask CareFlow is for the hospital director. Your role doesn&apos;t include it.</Panel>
      </>
    );

  async function open(id: string) {
    setOpening(id);
    try {
      const s = await getAskSession(id);
      state.load(s.id, s.turns.map(turnToAnswer));
    } finally {
      setOpening(null);
    }
  }

  return (
    <>
      <PageHeader title="Ask CareFlow">
        <p>Ask about discharge operations in plain English. Every number in an answer is checked against your hospital&apos;s data, and you can see the numbers behind it.</p>
      </PageHeader>
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <Panel strong className="min-w-0 p-5">
          <Conversation state={state} />
        </Panel>
        <Panel as="aside" className="h-fit p-4" >
          <div className="mb-2 flex items-center justify-between gap-2">
            <h2 className="flex items-center gap-1.5 text-sm font-bold text-ink"><History className="size-4" aria-hidden /> Your questions</h2>
            <Button variant="quiet" onClick={state.reset} className="!px-2 !py-1 text-xs"><MessageSquarePlus className="size-3.5" aria-hidden /> New</Button>
          </div>
          {sessions.isPending && <p className="text-sm text-faint">Loading…</p>}
          {sessions.error && <p role="alert" className="text-sm text-coral">{(sessions.error as Error).message}</p>}
          {sessions.data?.length === 0 && <p className="text-sm text-slate">Nothing yet. Your past questions will appear here.</p>}
          <ul className="flex flex-col gap-1">
            {sessions.data?.map((s) => (
              <li key={s.id}>
                <button type="button" onClick={() => open(s.id)} aria-current={state.sessionId === s.id ? "true" : undefined}
                  className={clsx("w-full rounded-xl px-2.5 py-2 text-left text-sm hover:bg-white/70", state.sessionId === s.id ? "bg-white/80 font-semibold text-ink" : "text-slate")}>
                  <span className="line-clamp-2">{s.title}</span>
                  <span className="text-xs text-faint">{s.turns} question{s.turns === 1 ? "" : "s"} · {new Date(s.updatedAt).toLocaleDateString()}{opening === s.id ? " · opening…" : ""}</span>
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      </div>
    </>
  );
}
