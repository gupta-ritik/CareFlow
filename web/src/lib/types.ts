// Mirrors backend/app/schemas.py (camelCase on the wire).

export type Department = "documentation" | "pharmacy" | "insurance" | "billing" | "laboratory";

export type TaskStatus = "cleared" | "in_progress" | "blocked" | "not_started";

export type WorkflowStatus = "in_progress" | "blocked" | "awaiting_approval" | "cleared" | "discharged";

export type RiskLevel = "low" | "medium" | "high";

export interface PatientContext {
  patientId: string;
  displayName: string; // synthetic data only
  mrn: string;
  ward: string;
  bed: string;
  payer: string;
  attending: string;
  admittedAt: string;
}

export interface DepartmentTask {
  id: string;
  department: Department;
  title: string;
  status: TaskStatus;
  owner: string;
  updatedAt: string;
  dependsOn: Department[];
}

export interface Evidence {
  id: string;
  source: string;
  query: string;
  observed: string;
  observedAt: string;
  untrusted?: boolean;
}

export interface Blocker {
  id: string;
  department: Department;
  kind: string;
  summary: string;
  since: string;
  evidenceIds: string[];
  waitingOn?: Department | null;
}

export type ActionStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "info_requested"
  | "auto_approved"
  | "executed"
  | "verified"
  | "superseded"
  | "verification_failed"
  | "execution_failed";

export interface ProposedAction {
  id: string;
  workflowId: string;
  blockerId: string;
  action: string;
  description: string;
  expectedImpact: string;
  risk: RiskLevel;
  requiresApproval: boolean;
  approverRole: string;
  evidenceIds: string[];
  sopRefs: string[];
  status: ActionStatus;
  createdAt: string;
  decidedBy?: string | null;
  decidedAt?: string | null;
  comment?: string | null;
}

export type ActorKind = "agent" | "human" | "system";

export interface AuditEvent {
  id: string;
  workflowId: string;
  at: string;
  actorKind: ActorKind;
  actor: string;
  event: string;
  detail: string;
  evidenceIds?: string[];
  latencyMs?: number | null;
  tokens?: number | null;
}

export interface Workflow {
  id: string;
  patient: PatientContext;
  authorizedAt: string;
  authorizedBy: string;
  status: WorkflowStatus;
  summary?: string | null;
  tasks: DepartmentTask[];
  blockers: Blocker[];
  evidence: Evidence[];
}

export interface AgentStats {
  agent: string;
  calls: number;
  llmCalls: number;
  fallbackCalls: number;
  avgLatencyMs: number;
  tokens: number;
  retries: number;
  errors: number;
}

export interface Dashboard {
  activeWorkflows: number;
  awaitingApproval: number;
  blocked: number;
  readyToDischarge: number;
  discharged: number;
  medianWaitMinutes: number;
  avgClearanceMinutes: number | null;
  completionRate: number;
  humanInterventionRate: number;
  recoveryRate: number | null;
  avgDecisionMinutes: number | null;
  statusCounts: Record<string, number>;
  departments: { department: Department; openBlockers: number; pendingTasks: number; avgBlockedMinutes: number }[];
  blockerKinds: { kind: string; count: number }[];
  actions: { total: number; automatic: number; needingApproval: number; verified: number; verificationFailed: number };
  clearanceTimes: { workflowId: string; minutes: number }[];
}

export interface SessionUser {
  name: string;
  email: string;
  role: string;
  permissions: string[];
  /** Signed in through "Try the demo" (synthetic demo hospital, no password). */
  demo?: boolean;
  demoRole?: string;
}

export interface DemoRole {
  key: string;
  role: string;
  home: string;
  description: string;
}

export interface AlertChannel {
  id: string;
  kind: "webpush" | "telegram";
  label: string;
  createdAt: string;
  endpoint: string | null;
}

export interface AlertSettings {
  webPush: { enabled: boolean; publicKey: string | null };
  telegram: { enabled: boolean };
  demo: boolean;
  channels: AlertChannel[];
}

export type DemoInfo = { enabled: false } | { enabled: true; preparing: boolean; resetHours: number; roles: DemoRole[] };

export type PayerType = "self_pay" | "cashless" | "government";

export interface PatientRecord {
  ref: string;
  displayName: string;
  mrn: string;
  ward: string;
  bed: string;
  payer: string;
  payerType: PayerType;
  attending: string;
  admittedAt: string;
  hasLabOrders: boolean;
  consentForCoordination: boolean;
  status: "admitted" | "discharged";
  source: "manual" | "fhir";
  departments: Department[];
  workflow: { id: string; status: WorkflowStatus } | null;
}

export interface QueueItem {
  patientRef: string;
  displayName: string;
  mrn: string;
  ward: string;
  bed: string;
  payer: string;
  workflow: { id: string; status: WorkflowStatus; authorizedAt: string } | null;
  taskStatus: TaskStatus | null;
  record: Record<string, unknown> & { history?: { at: string; by: string; action: string; summary: string }[]; updated_at?: string };
  requests: { ref: string; at: string; kind: string; text: string }[];
  blockers: { id: string; kind: string; summary: string; since: string }[];
}

export interface DepartmentInfo {
  department: Department;
  canAct: boolean;
  actions: { name: string; label: string; params: string[] }[];
}

export interface UserAccount {
  id: string;
  name: string;
  email: string;
  role: string;
  isActive: boolean;
  createdAt: string;
  lastLoginAt: string | null;
  locked: boolean;
}

export interface RoleInfo {
  name: string;
  description: string;
  permissions: string[];
}

export interface ClearanceReport {
  hospital: string;
  workflowId: string;
  generatedAt: string;
  generatedBy: string;
  patient: { displayName: string; mrn: string; ward: string; bed: string; payer: string; payerType: PayerType; attending: string; admittedAt: string };
  status: WorkflowStatus;
  authorizedAt: string;
  authorizedBy: string;
  clearedAt: string | null;
  dischargedAt: string | null;
  clearanceMinutes: number | null;
  elapsedMinutes: number | null;
  summary: string | null;
  departments: { department: Department; title: string; status: TaskStatus; owner: string; clearedAt: string | null; minutesToClear: number | null; blockers: number }[];
  blockers: { id: string; department: Department; kind: string; summary: string; since: string; resolvedAt: string | null; status: string; minutesOpen: number | null }[];
  actions: { id: string; action: string; description: string; risk: RiskLevel; requiresApproval: boolean; approverRole: string; status: ActionStatus; createdAt: string; decidedBy: string | null; decidedAt: string | null; comment: string | null }[];
  timeline: { at: string; actorKind: ActorKind; actor: string; event: string; detail: string }[];
}

// ---- director, beds, integrations --------------------------------------------------------------------
export interface StatBlock {
  count: number;
  avg: number | null;
  median: number | null;
  p90: number | null;
}

export interface ExecutiveOverview {
  generatedAt: string;
  windowDays: number;
  census: {
    inHospital: number;
    awaitingAuthorization: number;
    inClearance: number;
    blocked: number;
    awaitingApproval: number;
    readyToDischarge: number;
    dischargedToday: number;
    dischargedInWindow: number;
  };
  flow: {
    clearanceMinutes: StatBlock;
    authorizationToExitMinutes: StatBlock;
    readyToExitMinutes: StatBlock;
    slaBreaches: number;
    blockersFound: number;
    blockersResolved: number;
  };
  trend: { date: string; authorized: number; discharged: number; medianClearanceMinutes: number | null }[];
  departments: {
    department: Department;
    openBlockers: number;
    pendingTasks: number;
    avgMinutesToClear: number | null;
    slaBreaches: number;
    oldestBlockerMinutes: number | null;
  }[];
  bottleneck: Department | null;
  approvals: {
    pending: number;
    byRole: Record<string, number>;
    byRisk: Record<string, number>;
    oldestPendingMinutes: number | null;
    avgDecisionMinutes: number | null;
    decided: number;
    rejected: number;
    automaticActions: number;
  };
  longestWaits: {
    workflowId: string;
    patient: string;
    mrn: string;
    ward: string;
    bed: string;
    status: WorkflowStatus;
    waitMinutes: number;
    blockingDepartments: Department[];
    payerType: PayerType;
  }[];
  payerMix: Partial<Record<PayerType, number>>;
  beds: {
    total: number;
    available: number;
    occupied: number;
    cleaning: number;
    blocked: number;
    occupancy: number | null;
    byWard: { ward: string; total: number; available?: number; occupied?: number; cleaning?: number; blocked?: number }[];
  };
  integrations: Record<IntegrationSystem, { messages24h: number; errors24h: number; lastMessageAt: string | null }>;
  ai: { runs: number; llm: number; fallback: number; deterministic: number; errors: number; tokens: number };
  staffActivity: { actor: string; actions: number }[];
}

export type BedStatus = "available" | "occupied" | "cleaning" | "blocked";

export interface BedInfo {
  id: string;
  ward: string;
  bed: string;
  status: BedStatus;
  patientRef: string | null;
  patientName: string | null;
  patientStatus: string | null;
  updatedAt: string;
  updatedBy: string;
}

export interface BedBoard {
  beds: BedInfo[];
  summary: { total: number; occupancy: number | null } & Record<BedStatus, number>;
}

export type IntegrationSystem = "his" | "tpa" | "pharmacy" | "lis";

export interface IntegrationSystemStatus {
  system: IntegrationSystem;
  label: string;
  activeKeys: number;
  outboundConfigured: boolean;
  hl7OutboundConfigured: boolean | null;
  inbound24h: number;
  outbound24h: number;
  errors24h: number;
  lastMessageAt: string | null;
  health: "idle" | "ok" | "degraded";
}

export interface IntegrationStatus {
  systems: IntegrationSystemStatus[];
  mllp: { port: number; allowedHosts: string[]; tenant: string | null };
  signing: boolean;
}

export interface IntegrationMessageRow {
  id: string;
  at: string;
  direction: "inbound" | "outbound";
  system: IntegrationSystem;
  protocol: "hl7v2" | "webhook";
  type: string;
  controlId: string;
  patientRef: string | null;
  status: string;
  detail: string;
  attempts: number;
  raw?: string;
}

export interface IntegrationKeyRow {
  id: string;
  name: string;
  system: IntegrationSystem | "any";
  prefix: string;
  isActive: boolean;
  createdBy: string;
  createdAt: string;
  lastUsedAt: string | null;
}
