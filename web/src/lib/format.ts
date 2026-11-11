import type { ActionStatus, Department, RiskLevel, TaskStatus, WorkflowStatus } from "./types";

export function minutesSince(iso: string, now = Date.now()) {
  return Math.max(0, Math.round((now - new Date(iso).getTime()) / 60_000));
}

export function duration(minutes: number) {
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m ? `${h}h ${m}m` : `${h}h`;
}

export function since(iso: string) {
  const m = minutesSince(iso);
  return m < 1 ? "just now" : `${duration(m)} ago`;
}

export function clock(iso: string) {
  return new Date(iso).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
}

export const DEPT_LABEL: Record<Department, string> = {
  documentation: "Documentation",
  pharmacy: "Pharmacy",
  insurance: "Insurance",
  billing: "Billing",
  laboratory: "Laboratory",
};

export const TASK_LABEL: Record<TaskStatus, string> = {
  cleared: "Cleared",
  in_progress: "In progress",
  blocked: "Blocked",
  not_started: "Not started",
};

export const WORKFLOW_LABEL: Record<WorkflowStatus, string> = {
  in_progress: "Clearing",
  blocked: "Blocked",
  awaiting_approval: "Needs approval",
  cleared: "Ready to discharge",
  discharged: "Discharged",
};

export const ACTION_LABEL: Record<ActionStatus, string> = {
  pending: "Waiting for approval",
  approved: "Approved, executing",
  rejected: "Rejected",
  info_requested: "More information requested",
  auto_approved: "Running automatically (low risk)",
  executed: "Executed, verifying",
  verified: "Done and verified",
  superseded: "Skipped: blocker already gone",
  verification_failed: "Verification failed: no change found",
  execution_failed: "Failed to execute",
};

export const ACTION_SHORT: Record<ActionStatus, string> = {
  pending: "Pending",
  approved: "Approved",
  rejected: "Rejected",
  info_requested: "Info requested",
  auto_approved: "Automatic",
  executed: "Executed",
  verified: "Verified",
  superseded: "Skipped",
  verification_failed: "Not applied",
  execution_failed: "Failed",
};

export const KIND_LABEL: Record<string, string> = {
  pharmacy_delay: "Pharmacy delay",
  reconciliation_incomplete: "Reconciliation incomplete",
  document_incomplete: "Incomplete document",
  payer_query: "Payer query",
  payer_delay: "Payer delay",
  payer_denied: "Payer denied",
  not_submitted: "Not submitted",
  system_unavailable: "System unreachable",
  billing_delay: "Billing delay",
  lab_unacknowledged: "Unacknowledged lab report",
  waiting_on_dependency: "Waiting on another department",
  action_not_applied: "Action not applied",
  no_record: "Record missing",
};

export const RISK_LABEL: Record<RiskLevel, string> = {
  low: "Low risk",
  medium: "Medium risk",
  high: "High risk",
};
