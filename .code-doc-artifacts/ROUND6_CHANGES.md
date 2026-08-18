# C.O.R.E. CAMP — Round 6: Phase 2-5 Parsers, Review Queue, Commit Layer

Implements Phases 2 through 5 of ROUND5_PLAN_document_ingestion.md: the
actual parsers, the human review queue, and the commit layer that turns an
approved extraction into real data.

## New package: `app/ingestion/`

- **`schema.py`** — `PendingExtractions` (review queue) + `ExtractionAuditLog`
  (immutable decision log), plus five new reference tables:
  `PartsCatalog`, `WiringReferences`, `ComponentSpecs`, `FaultIsolationRules`,
  `SystemSchematics`. Each committed row keeps a `source_document_id` back
  to the manual it came from - `MasterMEL` and `Directives` gained the same
  column so MMEL/AD/SB/ICA extractions (which write into *real* operational
  tables, not new reference ones) are traceable too.
- **`tier1_parsers.py`** — MMEL/IPC/WDM/CMM. Uses `pdfplumber` table
  extraction + header-keyword matching (case-insensitive substring, so
  "ATA Chapter" / "ATA Ch." / "Chapter" all resolve the same column).
  **No LLM, no external dependency beyond pdfplumber** (added to
  `requirements.txt`).
- **`tier3_parser.py`** — TSM/FIM, SSM, OEM Datasheet. Calls the Anthropic
  API directly (`urllib`, no new SDK dependency) with a schema-constrained
  extraction prompt. **Requires `ANTHROPIC_API_KEY` set in the server's own
  environment** - your key, your billing, exactly as agreed. If it's not
  set, parsing raises `Tier3NotConfigured` and the UI says so plainly
  instead of failing silently. Every Tier 3 row is capped at
  `TIER3_MAX_CONFIDENCE = 0.6` regardless of what the model reports, so it
  can never outrank a Tier 1 table read in the review queue.
- **`runner.py`** — dispatches an `IngestedDocuments` row to the right
  parser by tier and writes every candidate into `PendingExtractions`.
  Never writes to a real/reference table.
- **`commit.py`** — the *only* code path that writes an extraction into its
  target table, and only runs on an explicit `/documents/review/<id>/approve`
  POST. Column allowlist per target table (`COMMITTABLE_COLUMNS`) so a
  parser bug can't smuggle a field into a column it has no business
  touching. Rejections are kept, not deleted, for the audit trail.

## New routes (`app/routes/ingestion.py`) + UI

- `/documents/parse/<ingestion_id>` — trigger a parse (also a "Parse Now"
  button next to each doc on the Company Profile page).
- `/documents/review` — the Phase 4 review queue: every pending row across
  every document, confidence-badged (green ≥75%, amber ≥40%, red below),
  editable inline, Approve / Reject per row. New sidebar entry.

## Bug found and fixed while testing: keyword collision in the CMM/WDM parsers

Building real mock documents to test against (see below) surfaced a real
bug before it ever reached you: `'unit'` was listed as a match keyword for
both the `component_type` field *and* the actual `unit` field in the CMM
parser, so a table with both a "Component" and "Unit" column had the "Unit"
column's value (e.g. "degC") silently overwrite `component_type` instead of
landing in `unit`. Same latent collision existed in the WDM parser. Fixed
in both; re-verified against the mock CMM/WDM documents afterward.

## Testing performed

Built four realistic mock manuals (ATA iSpec 2200-style tables, via
reportlab) since no real samples exist yet - MMEL, IPC, WDM, CMM - and ran
the entire pipeline against them for real, not just unit tests in
isolation:

1. **Parser accuracy**: all four Tier 1 parsers, 100% field accuracy after
   the keyword-collision fix (verified against the mock docs' known values).
2. **Tier 3 response handling**: unit-tested `_parse_llm_response()` against
   a mocked Anthropic API response shape (no real network call, since no
   key is configured) - confirmed the 0.6 confidence cap is enforced even
   when the model reports higher, and confirmed `Tier3NotConfigured` raises
   cleanly with no key set.
3. **Full HTTP pipeline**: uploaded a mock MMEL through the real
   `/company-profile/upload-document` route → `/documents/parse/<id>` →
   `/documents/review` (confirmed it renders, confidence badges correct) →
   approved one row as-is, rejected one, approved one with an edited value
   → confirmed exactly the approved/edited rows landed in `MasterMEL` with
   the right `source_document_id`, the rejected one didn't, and
   `ExtractionAuditLog` captured all three decisions with the reviewer's
   username.
4. **Closed the loop**: called the existing MEL Tracker's own
   `/api/mmel/by-model/<model>` endpoint (built in an earlier round, before
   any of this existed) and confirmed the ingested-and-approved MMEL data
   is genuinely live there - an engineer opening the MEL Tracker today
   would see it as a normal MMEL reference item, no different from one
   entered by hand.
5. Full app route sweep still green after all changes.

## What's deliberately still open

- **Tier 3 needs your API key** to actually run against real documents -
  the code path is built and tested up to the network boundary, but I have
  no key to test the live call itself.
- **AD/SB/ICA (Tier 2)** commit into `Directives` via the same pipeline but
  weren't parser-tested this round (no Tier 2 mock built yet) - the field
  mapping in `commit.py` is written and ready, worth a real test pass next.
- **Reviewer role** (any Engineer vs. Admin-only) is still using whoever is
  logged in - the Open Decision from the plan doc is still open.
- Extracted `ComponentSpecs`/`WiringReferences`/`FaultIsolationRules` are
  reference data an engineer can consult, but nothing in
  `app/ontology_reasoner.py` reads them yet - wiring sensor thresholds from
  `ComponentSpecs` into the live SWRL reasoning is a deliberate follow-on
  step, not done here.
