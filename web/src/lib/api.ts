// Typed client for the CareFlow FastAPI backend.
import { useSession } from "./session";
import type {
  AgentStats,
  AlertChannel,
  AlertSettings,
  BedBoard,
  BedInfo,
  ExecutiveOverview,
  IntegrationKeyRow,
  IntegrationMessageRow,
  IntegrationStatus,
  AuditEvent,
  ClearanceReport,
  Dashboard,
  DemoInfo,
  DepartmentInfo,
  PatientRecord,
  ProposedAction,
  QueueItem,
  RoleInfo,
  SessionUser,
  UserAccount,
  Workflow,
} from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function send(path: string, init: RequestInit = {}): Promise<Response> {
  const token = useSession.getState().token;
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "content-type": "application/json",
        ...(token ? { authorization: `Bearer ${token}` } : {}),
        ...init.headers,
      },
    });
  } catch {
    throw new ApiError(0, `Can't reach the CareFlow server at ${API_BASE}. Check that the backend is running.`);
  }
  if (res.status === 401 && token) useSession.getState().signOut();
  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
    } catch {}
    throw new ApiError(res.status, message);
  }
  return res;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await send(path, init);
  return res.status === 204 ? (undefined as T) : res.json();
}

const post = <T,>(path: string, body?: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
const patch = <T,>(path: string, body: unknown) => request<T>(path, { method: "PATCH", body: JSON.stringify(body) });
const del = <T,>(path: string) => request<T>(path, { method: "DELETE" });

type Session = { accessToken: string; expiresIn: number; user: SessionUser };

// Auth and setup
export const setupStatus = () => request<{ needsSetup: boolean; fhirConfigured: boolean }>("/setup/status");
export const runSetup = (b: { hospitalName: string; adminName: string; adminEmail: string; password: string }) => post<Session>("/setup", b);
export const login = (email: string, password: string) => post<Session>("/auth/login", { email, password });
// Public demo (only when the backend runs with CAREFLOW_DEMO_MODE=true)
export const demoInfo = () => request<DemoInfo>("/demo");
export const demoLogin = (role: string) => post<Session & { home: string; preparing: boolean }>("/demo/login", { role });

// Alerts on phones: Web Push and Telegram
export const alertSettings = () => request<AlertSettings>("/notifications");
export const addPushSubscription = (b: { endpoint: string; keys: { p256dh: string; auth: string }; label: string }) =>
  post<AlertChannel>("/notifications/webpush", b);
export const telegramLink = () => post<{ url: string; expiresInMinutes: number }>("/notifications/telegram/link");
export const removeAlertChannel = (id: string) => del<void>(`/notifications/${id}`);
export const sendTestAlert = () => post<{ sent: number; channels: number }>("/notifications/test");

export const changePassword = (currentPassword: string, newPassword: string) =>
  post<void>("/auth/change-password", { currentPassword, newPassword });

// Users
export const listUsers = () => request<UserAccount[]>("/users");
export const listRoles = () => request<RoleInfo[]>("/roles");
export const createUser = (b: { name: string; email: string; role: string; password: string }) => post<UserAccount>("/users", b);
export const updateUser = (id: string, b: { role?: string; isActive?: boolean; password?: string }) => patch<UserAccount>(`/users/${id}`, b);

// Patients
export const listPatients = (status: "admitted" | "discharged" | "all", q = "") =>
  request<PatientRecord[]>(`/patients?status=${status}${q ? `&q=${encodeURIComponent(q)}` : ""}`);
export type NewPatient = {
  displayName: string;
  mrn: string;
  ward: string;
  bed: string;
  payer: string;
  payerType: string;
  attending: string;
  hasLabOrders: boolean;
  consentForCoordination: boolean;
};
export const admitPatient = (b: NewPatient) => post<PatientRecord>("/patients", b);
export const importFhirPatient = (b: { patientId: string; encounterId: string; payer: string; payerType: string; attending: string; consentForCoordination: boolean }) =>
  post<PatientRecord>("/patients/import-fhir", b);
export const updatePatient = (ref: string, b: Partial<Pick<PatientRecord, "ward" | "bed" | "attending" | "payer" | "hasLabOrders" | "consentForCoordination">>) =>
  patch<PatientRecord>(`/patients/${ref}`, b);
export const authorizeDischarge = (ref: string, note: string) =>
  post<{ workflowId: string; status: string }>(`/patients/${ref}/authorize-discharge`, { note: note || null });

// Workflows
export const listWorkflows = (status?: string) => request<Workflow[]>(`/workflows${status ? `?status=${status}` : ""}`);
export const getWorkflow = (id: string) => request<Workflow>(`/workflows/${id}`);
export const recheckWorkflow = (id: string) => post<{ rechecked: boolean }>(`/workflows/${id}/recheck`);
export const closeDischarge = (id: string) => post<{ status: string }>(`/workflows/${id}/discharge`);
export const getReport = (id: string) => request<ClearanceReport>(`/workflows/${id}/report`);

// Departments
export const myDepartments = () => request<DepartmentInfo[]>("/departments");
export const departmentQueue = (dept: string) => request<QueueItem[]>(`/departments/${dept}/queue`);
export const departmentAction = (dept: string, ref: string, action: string, params: Record<string, unknown> = {}) =>
  post<{ summary: string }>(`/departments/${dept}/${ref}/actions`, { action, params });

// Approvals, audit, analytics
export const listActions = () => request<ProposedAction[]>("/approvals");
export const listAudit = (workflowId?: string) =>
  request<AuditEvent[]>(`/audit${workflowId ? `?workflowId=${encodeURIComponent(workflowId)}` : ""}`);
export const listAgents = () => request<AgentStats[]>("/agents");
export const getDashboard = () => request<Dashboard>("/analytics/dashboard");

// Director, beds, integrations
export const getExecutive = (days: number) => request<ExecutiveOverview>(`/executive/overview?days=${days}`);
export const listBeds = () => request<BedBoard>("/beds");
export const addBeds = (ward: string, beds: string[]) => post<{ added: number }>("/beds", { ward, beds });
export const setBedStatus = (id: string, status: string) => patch<BedInfo>(`/beds/${id}`, { status });
export const integrationStatus = () => request<IntegrationStatus>("/integrations/status");
export const integrationMessages = (q: { system?: string; direction?: string; includeRaw?: boolean } = {}) => {
  const qs = new URLSearchParams();
  if (q.system) qs.set("system", q.system);
  if (q.direction) qs.set("direction", q.direction);
  if (q.includeRaw) qs.set("includeRaw", "true");
  return request<IntegrationMessageRow[]>(`/integrations/messages?${qs}`);
};
export const listIntegrationKeys = () => request<IntegrationKeyRow[]>("/integrations/keys");
export const createIntegrationKey = (name: string, system: string) =>
  post<IntegrationKeyRow & { key: string; note: string }>("/integrations/keys", { name, system });
export const revokeIntegrationKey = (id: string) => del<void>(`/integrations/keys/${id}`);
/** Sends an HL7 v2 message as the signed-in administrator; returns the ACK text. */
export async function sendHl7(text: string) {
  const res = await send("/integrations/hl7", { method: "POST", body: text, headers: { "content-type": "text/plain" } });
  return { ack: await res.text(), code: res.headers.get("x-hl7-ack-code") ?? "" };
}

export type Decision = "approve" | "reject" | "request_information";
const DECISION_PATH: Record<Decision, string> = { approve: "approve", reject: "reject", request_information: "request-information" };
export const decideAction = (id: string, decision: Decision, comment: string) =>
  post<ProposedAction>(`/approvals/${id}/${DECISION_PATH[decision]}`, { comment: comment || null });

/** Downloads the discharge CSV with the session token and saves it through the browser. */
export async function downloadCsv(from: string, to: string) {
  const qs = new URLSearchParams();
  if (from) qs.set("from", from);
  if (to) qs.set("to", to);
  const res = await send(`/reports/workflows.csv?${qs}`, { headers: { accept: "text/csv" } });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `careflow-discharges${from ? `-${from}` : ""}${to ? `-to-${to}` : ""}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}
