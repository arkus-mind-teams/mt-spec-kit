# Verification Report: [FEATURE NAME]

**Branch**: `[###-feature-name]` | **Date**: [DATE]
**Spec**: [spec.md](../[###-feature-name]/spec.md) | **Plan**: [plan.md](../[###-feature-name]/plan.md)

---

## Completeness

> Tasks checked against `specs/[###-feature-name]/tasks.md`

| Metric | Count |
|--------|-------|
| Total tasks | [TASKS_TOTAL] |
| Complete | [TASKS_COMPLETE] |
| Incomplete | [TASKS_INCOMPLETE] |

### Incomplete Tasks

| Task | Description | Severity |
|------|-------------|----------|
| [TASK-ID] | [TASK DESCRIPTION] | [CRITICAL \| WARNING] |

<!-- If no incomplete tasks: "All tasks complete." -->

---

## Spec Compliance Matrix

> Requirements and scenarios checked against `specs/[###-feature-name]/spec.md`

### Functional Requirements

| Requirement | Description | Evidence | Status |
|-------------|-------------|----------|--------|
| FR-xxx | [REQUIREMENT NAME] | [file path or "(none found)"] | [✅ IMPLEMENTED \| ⚠️ PARTIAL \| ❌ MISSING] |

### Acceptance Scenarios

| User Story | Scenario | Evidence | Status |
|------------|----------|----------|--------|
| US1 | [Given/When/Then condensed] | [file path or "(none found)"] | [✅ COVERED \| ⚠️ PARTIAL \| ❌ UNCOVERED] |

**Compliance Summary**: [N]/[TOTAL] requirements implemented · [M]/[TOTAL] scenarios covered

---

## Plan Coherence

> Technical decisions checked against `specs/[###-feature-name]/plan.md`

| Decision | Expected (plan.md) | Found (codebase) | Status |
|----------|--------------------|------------------|--------|
| [DECISION NAME] | [what plan.md specified] | [what codebase shows] | [✅ Followed \| ⚠️ Deviated] |

<!-- If plan.md missing: "(skipped — plan.md not found)" -->

---

## Issues Found

### CRITICAL

> Must resolve before feature is considered done.

- [DESCRIPTION] *(source: [completeness \| compliance \| coherence])*

<!-- If none: "None." -->

### WARNING

> Should review before merging; fix if practical.

- [DESCRIPTION] *(source: [completeness \| compliance \| coherence])*

<!-- If none: "None." -->

### SUGGESTION

> Optional improvements.

- [DESCRIPTION] *(source: [completeness \| compliance \| coherence])*

<!-- If none: "None." -->

---

## Verdict

**[PASS | PASS WITH WARNINGS | FAIL]**

[One-paragraph summary explaining the verdict. Mention the specific number of critical issues,
warnings, and suggestions. If FAIL, clearly state what must be resolved. If PASS WITH WARNINGS,
identify the most important warnings. If PASS, confirm the feature is ready to merge.]
