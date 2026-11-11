// Ask CareFlow client: streams Server-Sent Events from POST /api/ask, plus history, evidence and feedback.
import { API_BASE, ApiError } from "./api";
import { useSession } from "./session";

export type AskStatus = "answered" | "clarify" | "refused" | "no_data" | "error";

export interface AskClaim {
  result_id: string;
  field: string;
  value: number;
}

export interface AskSentence {
  text: string;
  claims: AskClaim[];
}

export interface AskChart {
  type: "line" | "bar";
  result_id: string;
  x: string;
  y: string;
  unit: string;
  title: string;
}

/** One aggregate (a whole period, one side of a comparison, or one group). `n` is "fewer than 5" when suppressed. */
export interface AskStat {
  n: number | string;
  median?: number | null;
  p90?: number | null;
  mean?: number | null;
  rate?: number | null;
  pct?: number | null;
  hits?: number;
  patients?: number | null;
  suppressed?: boolean;
}

export interface AskPeriod {
  start: string;
  end: string;
  label: string;
}

export interface AskResult {
  id: string;
  measure: string;
  label: string;
  unit: string;
  kind: "time" | "rate" | "count";
  noun: string;
  row_count: number;
  params: {
    measure: string;
    group_by: string[];
    filters: Record<string, string[]>;
    period: AskPeriod;
    compare: null | { kind: "period" | "rest"; period: AskPeriod | null; focus: Record<string, string[]>; focus_label: string; baseline_label: string };
  };
  total: AskStat;
  focus?: AskStat;
  baseline?: AskStat;
  groups: (AskStat & { key: Record<string, string> })[];
  baseline_groups?: (AskStat & { key: Record<string, string> })[];
}

export interface AskAnswer {
  type: "answer";
  status: AskStatus;
  turnId: string | null;
  sessionId: string | null;
  question: string;
  message: string;
  answer: string;
  sentences: AskSentence[];
  basedOn: string;
  followUps: string[];
  chart: AskChart | null;
  partial: boolean;
  results: AskResult[];
  mode: "llm" | "fallback";
  tokens: number;
  latencyMs: number;
  cached?: boolean;
}

export interface AskProgress {
  type: "progress";
  node: string;
  message: string;
}

export interface AskSessionRow {
  id: string;
  title: string;
  turns: number;
  createdAt: string;
  updatedAt: string;
}

export interface AskTurn {
  id: string;
  sessionId: string;
  question: string;
  status: AskStatus;
  partial: boolean;
  answer: string;
  message: string;
  sentences: AskSentence[];
  basedOn: string;
  followUps: string[];
  chart: AskChart | null;
  mode: string;
  rating: number | null;
  feedback: string | null;
  createdAt: string;
  results: AskResult[];
}

function headers(): Record<string, string> {
  const token = useSession.getState().token;
  return { "content-type": "application/json", accept: "text/event-stream", ...(token ? { authorization: `Bearer ${token}` } : {}) };
}

async function fail(res: Response): Promise<never> {
  if (res.status === 401) useSession.getState().signOut();
  let message = `Request failed (${res.status})`;
  try {
    message = (await res.json())?.error?.message ?? message;
  } catch {}
  throw new ApiError(res.status, message);
}

/** Asks a question and calls `onProgress` for each streamed step. Resolves with the final answer event. */
export async function askQuestion(question: string, sessionId: string | null, onProgress: (p: AskProgress) => void, signal?: AbortSignal): Promise<AskAnswer> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/ask`, { method: "POST", headers: headers(), body: JSON.stringify({ question, sessionId }), signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, `Can't reach the CareFlow server at ${API_BASE}. Check that the backend is running.`);
  }
  if (!res.ok || !res.body) await fail(res);
  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let final: AskAnswer | null = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buffer += decoder.decode(value, { stream: true });
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const block = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const data = block.split("\n").filter((l) => l.startsWith("data: ")).map((l) => l.slice(6)).join("\n");
      if (!data) continue;
      const event = JSON.parse(data) as AskProgress | AskAnswer;
      if (event.type === "progress") onProgress(event);
      else final = event;
    }
    if (done) break;
  }
  if (!final) throw new ApiError(0, "The answer stream ended early. Please try again.");
  return final;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: headers() });
  if (!res.ok) await fail(res);
  return res.json();
}

export const listAskSessions = () => get<AskSessionRow[]>("/ask/sessions");
export const getAskSession = (id: string) => get<{ id: string; title: string; turns: AskTurn[] }>(`/ask/sessions/${id}`);
export const getAskEvidence = (turnId: string) =>
  get<{ turnId: string; question: string; claims: AskClaim[]; results: AskResult[] }>(`/ask/turns/${turnId}/evidence`);

export async function sendAskFeedback(turnId: string, rating: 1 | -1, comment: string) {
  const res = await fetch(`${API_BASE}/ask/turns/${turnId}/feedback`, { method: "POST", headers: headers(), body: JSON.stringify({ rating, comment: comment || null }) });
  if (!res.ok) await fail(res);
}

/** A stored turn in the same shape as a streamed answer, for reopening history. */
export function turnToAnswer(t: AskTurn): AskAnswer {
  return {
    type: "answer", status: t.status, turnId: t.id, sessionId: t.sessionId, question: t.question, message: t.message,
    answer: t.answer, sentences: t.sentences, basedOn: t.basedOn, followUps: t.followUps, chart: t.chart, partial: t.partial,
    results: t.results, mode: t.mode === "llm" ? "llm" : "fallback", tokens: 0, latencyMs: 0,
  };
}

export const EXAMPLE_QUESTIONS = [
  "Why were discharges slow last Monday?",
  "Which TPA is slowest this month?",
  "Did billing get faster after day 90?",
  "Which ward has the most incomplete documents?",
  "Is pharmacy slower on weekend afternoons?",
  "What is the trend of discharge time over the last 90 days?",
];
