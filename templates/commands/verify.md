---
description: Verify implementation completeness and spec compliance after /speckit.implement
scripts:
  sh: scripts/bash/check-prerequisites.sh --json --paths-only
  ps: scripts/powershell/check-prerequisites.ps1 -Json -PathsOnly
---

## User Input

```text
$ARGUMENTS
```

You **MUST** consider the user input before proceeding (if not empty).

## Outline

1. **Resolve feature directory**: Run `{SCRIPT}` from the repo root. Parse the JSON output for `FEATURE_DIR`. For single quotes in args, use escape syntax (e.g. `'I'\''m Groot'`). All paths must be absolute.

   - If `FEATURE_DIR` is empty or the directory does not exist on disk, **halt immediately** and output an error in this format:

     ```
     ERROR: Feature directory not found.
     Branch: {current-git-branch}
     Expected: specs/{branch-derived-feature-name}/
     Available feature directories:
       - specs/001-example-feature/
       - specs/002-another-feature/
       (list actual subdirectories under specs/)
     ```

   - If `FEATURE_DIR` resolves successfully, derive these artifact paths from it:
     - `FEATURE_SPEC` = `{FEATURE_DIR}/spec.md`
     - `IMPL_PLAN`    = `{FEATURE_DIR}/plan.md`
     - `TASKS`        = `{FEATURE_DIR}/tasks.md`
     - `VERIFY_REPORT`= `{FEATURE_DIR}/verify-report.md`

2. **Load artifacts**: Attempt to read each artifact. Track which are present and which are missing. Record issues as you go:

   - Read `{TASKS}` → if missing, record: **WARNING** — "tasks.md not found; completeness check skipped"
   - Read `{FEATURE_SPEC}` → if missing, record: **CRITICAL** — "spec.md not found; spec compliance check skipped (spec is the source of truth)"
   - Read `{IMPL_PLAN}` → if missing, record: **WARNING** — "plan.md not found; plan coherence check skipped"

3. **Task completeness check** (skip if tasks.md is missing):

   Using the loaded `tasks.md` content:

   - Count all lines matching `- [ ]` (incomplete) and `- [x]` or `- [X]` (complete).
   - Record:
     - `tasks_total` = total count of both
     - `tasks_complete` = count of `[x]`/`[X]` lines
     - `tasks_incomplete` = count of `[ ]` lines
   - For each incomplete task line (`- [ ] ...`):
     - Extract the task ID and description
     - Assign severity:
       - **CRITICAL** — task belongs to a core phase: Setup, Foundation/Foundational, or any User Story implementation phase (any phase that is NOT the final Polish/Cross-Cutting phase)
       - **WARNING** — task belongs to a polish/optional phase (e.g., "Polish & Cross-Cutting Concerns", "Polish", or explicitly optional tasks)
     - Add to the incomplete tasks list with its severity
     - Add a corresponding issue at the same severity level: "{task-id}: {task-description}"

4. **Spec requirements compliance check** (skip if spec.md is missing):

   Using the loaded `spec.md` content:

   - Extract every functional requirement line matching the pattern `**FR-xxx**:` (e.g., `**FR-001**: ...`). Collect the requirement ID and its short description.
   - For each requirement, search the codebase (all files excluding `specs/`, `.git/`, `node_modules/`) for **structural evidence**: file names, function/class/method names, identifiers, config keys, or command names that clearly correspond to the requirement's intent. Use your read and search tools to look for this evidence.
   - Assign a status based on what you find:
     - `✅ IMPLEMENTED` — clear, unambiguous evidence found (e.g., a file, function, or config key directly implementing the requirement)
     - `⚠️ PARTIAL` — some evidence found but implementation appears incomplete or ambiguous (partial code, TODOs, stub-only)
     - `❌ MISSING` — no evidence found in the codebase
   - Record each result as a Compliance Record: `{requirement_id, requirement_name, evidence (file path or "(none found)"), status}`
   - Add issues for non-passing statuses:
     - `❌ MISSING` → **CRITICAL** issue: "FR-xxx ({name}) has no implementation evidence"
     - `⚠️ PARTIAL` → **WARNING** issue: "FR-xxx ({name}) has only partial implementation evidence"

5. **Acceptance scenario compliance check** (skip if spec.md is missing):

   Using the loaded `spec.md` content:

   - Extract every Given/When/Then acceptance scenario from the User Scenarios section. For each scenario, note the user story it belongs to (US1, US2, etc.) and condense the scenario to a short description.
   - For each scenario, search the codebase for **behavioral evidence**: implementation of the Given precondition handling, the When trigger/action, and the Then expected outcome. Look for command handlers, validation logic, output-writing code, or error-handling paths that match the scenario's behavior.
   - Assign a status:
     - `✅ COVERED` — implementation evidence found for the full scenario (Given + When + Then)
     - `⚠️ PARTIAL` — evidence found for part of the scenario (e.g., When is implemented but Then output is missing)
     - `❌ UNCOVERED` — no evidence found for this scenario
   - Record each result as a Scenario Record: `{user_story, scenario_description, evidence, status}`
   - Add issues for non-passing statuses:
     - `❌ UNCOVERED` → **CRITICAL** issue: "Scenario ({user_story}: {scenario_description}) is not covered"
     - `⚠️ PARTIAL` → **WARNING** issue: "Scenario ({user_story}: {scenario_description}) is only partially covered"
   - Compute the compliance summary string:
     - `requirements_implemented` = count of `✅ IMPLEMENTED` compliance records
     - `requirements_total` = total compliance records
     - `scenarios_covered` = count of `✅ COVERED` scenario records
     - `scenarios_total` = total scenario records
     - Summary: `"{requirements_implemented}/{requirements_total} requirements implemented · {scenarios_covered}/{scenarios_total} scenarios covered"`

6. **Plan coherence check** (skip if plan.md is missing):

   Using the loaded `plan.md` content:

   - Extract key technical decisions from the **"Technical Decisions Summary"** table (rows with Decision / Choice / Rationale columns) and from the **"Project Structure"** section (file paths and directory layout described as planned).
   - For each decision, search the codebase for evidence that the decision was followed:
     - Check that the technology, approach, or file structure described in plan.md is actually present in the implementation
     - Compare plan.md's stated architecture against what exists on disk
   - Assign a status:
     - `✅ Followed` — the implementation matches what plan.md specified
     - `⚠️ Deviated` — the implementation diverges from plan.md (use a note explaining what was specified vs. what was found)
   - Record each result as a Coherence Record: `{decision_name, expected, found, status}`
   - Add issues for deviations:
     - `⚠️ Deviated` → **WARNING** issue: "Plan deviation: {decision_name} — plan specified '{expected}', found '{found}'" (deviations are WARNING, not CRITICAL — they may be intentional improvements)
   - If plan.md was missing (recorded in Step 2), skip this step entirely; the WARNING was already recorded.

7. **Assemble issues**: Collect all issues recorded in Steps 2–6 and organize them into three lists:
   - `critical_issues` — all CRITICAL severity issues
   - `warning_issues` — all WARNING severity issues
   - `suggestion_issues` — all SUGGESTION severity issues

8. **Derive verdict**:

   ```
   IF critical_issues is non-empty:
     verdict = FAIL
   ELSE IF warning_issues or suggestion_issues is non-empty:
     verdict = PASS WITH WARNINGS
   ELSE:
     verdict = PASS
   ```

9. **Write verify-report.md**: Write the file at `{VERIFY_REPORT}`, overwriting any existing version (idempotent). Use the structure defined in `templates/verify-report-template.md` as the canonical reference for section order and formatting. Populate every section with real data collected in Steps 3–8:

   - **Header**: branch name, today's date (YYYY-MM-DD), relative link to spec.md, relative link to plan.md
   - **Completeness section**: fill the metrics table with `tasks_total`, `tasks_complete`, `tasks_incomplete`; list each incomplete task with its ID, description, and severity; if all tasks complete, write "All tasks complete."; if tasks.md was missing, write "(skipped — tasks.md not found)"
   - **Spec Compliance Matrix — Functional Requirements**: one table row per FR-xxx with requirement ID, name, evidence path (or "(none found)"), and status emoji; if spec.md was missing, write "(skipped — spec.md not found)"
   - **Spec Compliance Matrix — Acceptance Scenarios**: one table row per scenario with user story, condensed scenario description, evidence, and status emoji; include the compliance summary line below the table
   - **Plan Coherence**: one table row per decision with decision name, expected value, found value, and status emoji; if plan.md was missing, write "(skipped — plan.md not found)"
   - **Issues Found**: list each issue under its severity heading (CRITICAL / WARNING / SUGGESTION); write "None." under any heading with no issues
   - **Verdict**: bold the verdict label (`**PASS**` / `**PASS WITH WARNINGS**` / `**FAIL**`), then write a one-paragraph summary explaining the result, the total count of critical issues, warnings, and suggestions, and what must be resolved before the feature is considered done (if anything)

   After writing, confirm the result to the user:

   ```
   Verification report written to: specs/{feature-name}/verify-report.md
   Verdict: {PASS | PASS WITH WARNINGS | FAIL}
   ```
