# Using a real FHIR server

1. Start HAPI FHIR: `docker compose --profile fhir up hapi` (http://localhost:8080/fhir).
2. Generate synthetic patients with Synthea (https://github.com/synthetichealth/synthea):
   `./run_synthea -p 20 --exporter.fhir.transaction_bundle true`
3. Load each bundle: `curl -X POST -H "Content-Type: application/fhir+json" --data @output/fhir/<file>.json http://localhost:8080/fhir`
4. Point CareFlow at it: `CAREFLOW_FHIR_BASE_URL=http://hapi:8080/fhir`.
5. Each CareFlow patient row needs `ref` = the FHIR Patient id and `encounter_ref` = the Encounter id.

On every run `load_context` reads the server's CapabilityStatement, then Patient, Encounter and (if the server supports
`ServiceRequest?encounter=`) lab orders. If the FHIR read fails, the run continues on the stored context and the audit
trail says so. Writes use `If-Match` with the last seen version; a 409/412 raises `FhirVersionConflict`.
