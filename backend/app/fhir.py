"""FHIR R4 integration.

`FhirClient` talks to any R4 server (HAPI FHIR in docker-compose). It reads the CapabilityStatement first and
refuses searches the server doesn't declare, follows Bundle `next` links for pagination, and keeps resource
versions so writes can use If-Match. `map_patient_context` turns Patient + Encounter + ServiceRequest into the
administrative context CareFlow needs; nothing clinical beyond "lab orders exist" is kept.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx


class FhirError(Exception):
    pass


class FhirUnsupported(FhirError):
    pass


class FhirVersionConflict(FhirError):
    pass


@dataclass
class Capability:
    resources: dict[str, set[str]] = field(default_factory=dict)  # resource type -> supported search params

    def supports(self, resource: str, param: str | None = None) -> bool:
        if resource not in self.resources:
            return False
        return param is None or param in self.resources[resource] or param.startswith("_")


class FhirClient:
    def __init__(self, base_url: str, token: str | None = None, transport: httpx.AsyncBaseTransport | None = None, timeout: float = 15.0):
        headers = {"Accept": "application/fhir+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, transport=transport, timeout=timeout)
        self._capability: Capability | None = None
        self.versions: dict[str, str] = {}  # "Task/123" -> versionId

    async def aclose(self):
        await self._http.aclose()

    async def _get(self, url: str, params: dict | None = None) -> dict:
        r = await self._http.get(url, params=params)
        if r.status_code == 404:
            raise FhirError(f"Not found: {url}")
        r.raise_for_status()
        return r.json()

    async def capability(self) -> Capability:
        if self._capability is None:
            stmt = await self._get("/metadata")
            cap = Capability()
            for rest in stmt.get("rest", []):
                for res in rest.get("resource", []):
                    cap.resources[res["type"]] = {p["name"] for p in res.get("searchParam", [])}
            self._capability = cap
        return self._capability

    def _track(self, resource: dict) -> dict:
        vid = resource.get("meta", {}).get("versionId")
        if vid and resource.get("id"):
            self.versions[f"{resource['resourceType']}/{resource['id']}"] = vid
        return resource

    async def read(self, resource: str, rid: str) -> dict:
        cap = await self.capability()
        if not cap.supports(resource):
            raise FhirUnsupported(f"Server does not support {resource}")
        return self._track(await self._get(f"/{resource}/{rid}"))

    async def search(self, resource: str, params: dict[str, str], max_pages: int = 20) -> list[dict]:
        cap = await self.capability()
        for name in params:
            if not cap.supports(resource, name):
                raise FhirUnsupported(f"Server does not support {resource}?{name}")
        out: list[dict] = []
        bundle = await self._get(f"/{resource}", params=params)
        for _ in range(max_pages):
            out += [self._track(e["resource"]) for e in bundle.get("entry", []) if "resource" in e]
            nxt = next((link["url"] for link in bundle.get("link", []) if link.get("relation") == "next"), None)
            if not nxt:
                break
            bundle = await self._get(nxt)
        return out

    async def create(self, resource: dict) -> dict:
        r = await self._http.post(f"/{resource['resourceType']}", json=resource, headers={"Content-Type": "application/fhir+json"})
        r.raise_for_status()
        return self._track(r.json())

    async def update(self, resource: dict) -> dict:
        key = f"{resource['resourceType']}/{resource['id']}"
        headers = {"Content-Type": "application/fhir+json"}
        if key in self.versions:
            headers["If-Match"] = f'W/"{self.versions[key]}"'
        r = await self._http.put(f"/{key}", json=resource, headers=headers)
        if r.status_code in (409, 412):
            raise FhirVersionConflict(f"{key} changed on the server (sent version {self.versions.get(key)})")
        r.raise_for_status()
        return self._track(r.json())


def _human_name(patient: dict) -> str:
    for n in patient.get("name", []):
        if n.get("text"):
            return n["text"]
        given = " ".join(n.get("given", []))
        return f"{given} {n.get('family', '')}".strip()
    return "Unknown"


def _mrn(patient: dict) -> str:
    for ident in patient.get("identifier", []):
        codings = ident.get("type", {}).get("coding", [])
        if any(c.get("code") == "MR" for c in codings):
            return f"MRN {ident.get('value')}"
    return f"MRN {patient.get('id')}"


def map_patient_context(patient: dict, encounter: dict, service_requests: list[dict]) -> dict[str, Any]:
    location = ""
    for loc in encounter.get("location", []):
        location = loc.get("location", {}).get("display", location)
    ward, _, bed = location.partition(",")
    start = encounter.get("period", {}).get("start")
    lab = any(
        any(c.get("code") == "108252007" or c.get("display", "").lower().startswith("lab") for cat in sr.get("category", []) for c in cat.get("coding", []))
        for sr in service_requests
        if sr.get("status") in ("active", "completed")
    )
    return {
        "patient_ref": patient["id"],
        "encounter_ref": encounter["id"],
        "display_name": _human_name(patient),
        "mrn": _mrn(patient),
        "ward": ward.strip() or "Unassigned",
        "bed": bed.strip(),
        "admitted_at": datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None,
        "has_lab_orders": lab,
    }


async def fetch_patient_context(client: FhirClient, patient_ref: str, encounter_ref: str) -> dict[str, Any]:
    patient = await client.read("Patient", patient_ref)
    encounter = await client.read("Encounter", encounter_ref)
    cap = await client.capability()
    srs = []
    if cap.supports("ServiceRequest", "encounter"):
        srs = await client.search("ServiceRequest", {"encounter": f"Encounter/{encounter_ref}"})
    return map_patient_context(patient, encounter, srs)
