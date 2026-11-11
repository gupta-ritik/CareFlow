"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as api from "./api";
import { useCan, useSession } from "./session";

// Background workers process integration events, so screens poll to pick up their results.
const LIVE = 5000;

function useAuthed() {
  return useSession((s) => Boolean(s.token));
}

export const useWorkflows = (status?: string) =>
  useQuery({ queryKey: ["workflows", status ?? "open"], queryFn: () => api.listWorkflows(status), enabled: useAuthed(), refetchInterval: LIVE });

export const useWorkflow = (id: string) =>
  useQuery({ queryKey: ["workflow", id], queryFn: () => api.getWorkflow(id), enabled: useAuthed(), refetchInterval: LIVE });

export const useReport = (id: string) => useQuery({ queryKey: ["report", id], queryFn: () => api.getReport(id), enabled: useAuthed() });

export const useActions = () => useQuery({ queryKey: ["actions"], queryFn: api.listActions, enabled: useAuthed(), refetchInterval: LIVE });

export const useAudit = (workflowId?: string) =>
  useQuery({ queryKey: ["audit", workflowId ?? "all"], queryFn: () => api.listAudit(workflowId), enabled: useAuthed(), refetchInterval: LIVE });

export const useAgents = () => {
  const allowed = useCan("audit:read");
  return useQuery({ queryKey: ["agents"], queryFn: api.listAgents, enabled: useAuthed() && allowed, refetchInterval: 15000, retry: false });
};

export const useDashboard = () => {
  const allowed = useCan("analytics:read");
  return useQuery({ queryKey: ["dashboard"], queryFn: api.getDashboard, enabled: useAuthed() && allowed, refetchInterval: 15000 });
};

export const usePatients = (status: "admitted" | "discharged" | "all", q: string) =>
  useQuery({ queryKey: ["patients", status, q], queryFn: () => api.listPatients(status, q), enabled: useAuthed(), refetchInterval: LIVE });

export const useMyDepartments = () => useQuery({ queryKey: ["departments"], queryFn: api.myDepartments, enabled: useAuthed() });

export const useQueue = (dept: string | undefined) =>
  useQuery({ queryKey: ["queue", dept], queryFn: () => api.departmentQueue(dept!), enabled: useAuthed() && Boolean(dept), refetchInterval: LIVE });

export const useUsers = () => useQuery({ queryKey: ["users"], queryFn: api.listUsers, enabled: useAuthed() });
export const useRoles = () => useQuery({ queryKey: ["roles"], queryFn: api.listRoles, enabled: useAuthed(), staleTime: Infinity });

function useInvalidateAll() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries();
}

export function useDecide() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (v: { id: string; decision: api.Decision; comment: string }) => api.decideAction(v.id, v.decision, v.comment),
    onSuccess: invalidate,
  });
}

export function useRecheck() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: api.recheckWorkflow, onSuccess: invalidate });
}

export function useCloseDischarge() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: api.closeDischarge, onSuccess: invalidate });
}

export function useAdmit() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: api.admitPatient, onSuccess: invalidate });
}

export function useImportFhir() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: api.importFhirPatient, onSuccess: invalidate });
}

export function useUpdatePatient() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (v: { ref: string; body: Parameters<typeof api.updatePatient>[1] }) => api.updatePatient(v.ref, v.body),
    onSuccess: invalidate,
  });
}

export function useAuthorize() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: (v: { ref: string; note: string }) => api.authorizeDischarge(v.ref, v.note), onSuccess: invalidate });
}

export function useDepartmentAction() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (v: { dept: string; ref: string; action: string; params?: Record<string, unknown> }) =>
      api.departmentAction(v.dept, v.ref, v.action, v.params),
    onSuccess: invalidate,
  });
}

export function useCreateUser() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: api.createUser, onSuccess: invalidate });
}

export function useUpdateUser() {
  const invalidate = useInvalidateAll();
  return useMutation({
    mutationFn: (v: { id: string; body: Parameters<typeof api.updateUser>[1] }) => api.updateUser(v.id, v.body),
    onSuccess: invalidate,
  });
}

// ---- director, beds, integrations --------------------------------------------------------------------
export const useExecutive = (days: number) => {
  const allowed = useCan("executive:read");
  return useQuery({ queryKey: ["executive", days], queryFn: () => api.getExecutive(days), enabled: useAuthed() && allowed, refetchInterval: 15000 });
};

export const useBeds = () => useQuery({ queryKey: ["beds"], queryFn: api.listBeds, enabled: useAuthed(), refetchInterval: LIVE });

export function useBedMutations() {
  const invalidate = useInvalidateAll();
  return {
    add: useMutation({ mutationFn: (v: { ward: string; beds: string[] }) => api.addBeds(v.ward, v.beds), onSuccess: invalidate }),
    setStatus: useMutation({ mutationFn: (v: { id: string; status: string }) => api.setBedStatus(v.id, v.status), onSuccess: invalidate }),
  };
}

export const useIntegrationStatus = () => {
  const allowed = useCan("integration:read");
  return useQuery({ queryKey: ["integrations", "status"], queryFn: api.integrationStatus, enabled: useAuthed() && allowed, refetchInterval: 10000 });
};

export const useIntegrationMessages = (system: string, direction: string) => {
  const allowed = useCan("integration:read");
  return useQuery({
    queryKey: ["integrations", "messages", system, direction],
    queryFn: () => api.integrationMessages({ system: system || undefined, direction: direction || undefined, includeRaw: true }),
    enabled: useAuthed() && allowed,
    refetchInterval: LIVE,
  });
};

export const useIntegrationKeys = () => {
  const allowed = useCan("user:manage");
  return useQuery({ queryKey: ["integrations", "keys"], queryFn: api.listIntegrationKeys, enabled: useAuthed() && allowed });
};

export function useIntegrationKeyMutations() {
  const invalidate = useInvalidateAll();
  return {
    create: useMutation({ mutationFn: (v: { name: string; system: string }) => api.createIntegrationKey(v.name, v.system), onSuccess: invalidate }),
    revoke: useMutation({ mutationFn: api.revokeIntegrationKey, onSuccess: invalidate }),
  };
}

export function useSendHl7() {
  const invalidate = useInvalidateAll();
  return useMutation({ mutationFn: api.sendHl7, onSuccess: invalidate });
}
