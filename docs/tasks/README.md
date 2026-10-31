# Tasks

One file per task, so work survives a lost chat. `open/` = not finished, `done/` = finished (moved, never deleted).
File name `T-NNN-short-slug.md`, numbers never reused. Next free number: **T-007**.
Each file: Status (`todo` / `doing` / `blocked: <why>` / `parked: <why>`), Who (`Claude` or `owner`), Why, Done when,
Notes. When done: set `done YYYY-MM-DD` + what was checked, `git mv` to `done/`, update [../STATUS.md](../STATUS.md)
and [../LOG.md](../LOG.md). Run the leak scan before committing.

| Task | Who | Status |
|---|---|---|
| [T-001 Demo video](open/T-001-demo-video.md) | owner | todo |
| [T-002 Review Arabic golden questions](open/T-002-review-arabic-golden.md) | owner | todo |
| [T-003 Document provenance / trusted sources](open/T-003-provenance.md) | Claude | todo |
| [T-004 Shared answer cache](open/T-004-shared-answer-cache.md) | Claude | todo |
| [T-005 pgvector iterative scans locally](open/T-005-pgvector-iterative-scans.md) | Claude | todo |

Done: [T-006 Keep reasoning text out of answers](done/T-006-no-reasoning-text.md) (2026-10-31).

Finished build steps are documented in [../steps/](../steps/) and the LOG.
