# AGENTS.md

This file gives AI coding agents project-specific operating instructions for this repository.

Status: Advisory v1.
Enforcement: Advisory (not mandatory gate).

---

## 1) Project Intent

`spine` (formerly YamTrack) is a Django-based multi-media tracker with:
- Tracking for TV, seasons/episodes, movies, anime, manga, games, books, comics
- Diary logging
- Calendar/release events
- Notifications
- Import/export and webhook integrations
- User profiles, preferences, Hall of Fame, and custom lists

Current product direction from maintainer:
- Evolve from local/private tracker into a social platform (Letterboxd-style social behaviors) for all media types.

Source product intent reference:
- `spine-prd.md` (early PRD; directionally useful even if some implementation details have advanced).

---

## 2) Tech Stack (Current)

- Backend: Django 5.x (`src/`)
- Async/background: Celery + Redis
- DB: SQLite by default, PostgreSQL supported
- Templates/UI: Django templates + HTMX + Alpine + Tailwind build pipeline
- Auth: Django allauth
- History/auditing: `django-simple-history`
- API/data providers: TMDB, MAL, MangaUpdates, IGDB, OpenLibrary, Hardcover, ComicVine

---

## 3) Repository Map

- `src/app/`: Core tracking domain (models, forms, views, providers, statistics, template tags)
- `src/users/`: Custom user model, profile/settings/preferences
- `src/events/`: Calendar events, release processing, notifications, ICS export
- `src/lists/`: Custom collections and collaborators
- `src/integrations/`: Imports/exports + webhook handlers
- `src/templates/`: Shared and app-specific templates/components
- `src/config/`: Django settings, URLs, Celery/Gunicorn config
- `src/static/`: CSS/JS/static assets

Supporting docs/scripts:
- `LOCAL_SETUP.md`, `FRONTEND.md`, `start-dev.sh`
- `docker-compose*.yml`, `Dockerfile`, `entrypoint.sh`, `supervisord.conf`

---

## 4) Non-Negotiable Safety Rules

1. Do not commit or expose secrets from `.env` or settings defaults.
2. Do not remove/disable DB constraints casually, especially around `Item`, status enums, and uniqueness checks.
3. Preserve migration integrity:
   - Schema/model changes require migrations.
   - Never hand-edit old migrations unless explicitly requested.
   - Include migrations for social features and data model changes (prototypes included).
4. Destructive/contract-breaking migrations are not allowed.
5. Preserve async behavior:
   - If changing event, notification, diary, or import flows, validate Celery task triggering still works.
6. Avoid broad refactors unless explicitly requested; prefer minimal, bounded changes.
7. Do not break HTMX partial responses when modifying views/templates.
8. Do not introduce breaking URL changes without explicit migration/redirect plan.

---

## 5) Local Development Commands

Python/package workflow standard:
- Use `uv` with `uv.lock` for Python dependencies: the same set CI and the production image install. `uv run --locked <command>` keeps each checkout's or worktree's `.venv` in sync and runs the command.
- Use `npm` for frontend/Tailwind.

Recommended:
- `./start-dev.sh` (starts Tailwind watcher + Django dev server on `127.0.0.1:8001`)

Manual:
- `npm run build-css` (watch mode)
- `cd src && uv run --locked python manage.py runserver 127.0.0.1:8001` (8000 is production on the server Mac; see section 16)

Background services (when needed):
- Redis
- `cd src && CELERY_REDIS_URL=redis://localhost:6379/5 REDIS_PREFIX=<checkout>: uv run --locked celery -A config worker -l info` (never a worker on Redis DB 0; see section 16)
- `cd src && uv run --locked celery -A config beat -l info`

---

## 6) Testing and Validation

Primary checks before handing off non-trivial changes:

1. Targeted tests for changed area:
   - `cd src && uv run --locked python manage.py test <app_or_test_path>`
2. Before calling the work done: `scripts/check.sh` (ruff, models without a migration, the full suite), the same as CI's Fast checks.
3. For template/HTMX changes, verify:
   - Full-page load
   - Partial fragment response
   - No broken CSRF/HTMX headers
4. For Celery-triggering logic, verify task enqueue paths still execute.

Canonical test command (from repo docs): Django test runner via `python manage.py test`.
`pytest` config exists and can be used as supplemental tooling.

Lint/format policy (current):
- Required lint check: `ruff check src` (part of `scripts/check.sh` and CI's Fast checks in `.github/workflows/ci.yml`).
- Template formatting: `djlint` is available/configured but not currently a CI-required gate.
- Do not introduce new formatter/linter requirements unless explicitly requested.

---

## 7) Domain Guardrails

### 7.1 Core Tracking Models

Be careful when changing:
- `src/app/models.py`:
  - `Item` uniqueness/check constraints
  - `Media`/`BasicMedia` status and progress semantics
  - TV/Season/Episode relationship logic
  - Diary model behavior and constraints (target product direction: social diary across all media types)

### 7.2 User and Preferences

Be careful when changing:
- `src/users/models.py`:
  - Custom `User` preference fields and validation constraints
  - Enabled media type behavior (impacts UI visibility, search, events, stats)

### 7.3 Calendar and Notifications

Be careful when changing:
- `src/events/calendar.py`, `src/events/models.py`, `src/events/notifications.py`
  - Event generation/update cleanup
  - Sentinel datetime handling and sorting
  - Notification dedupe/exclusions

### 7.4 Integrations

Be careful when changing:
- `src/integrations/`:
  - Import modes (`new` vs `overwrite`)
  - OAuth callback handling
  - Webhook matching logic
  - CSV export field compatibility

---

## 8) Template/Frontend Guardrails

1. Keep existing server-rendered + HTMX architecture unless a task explicitly requests otherwise.
2. For components in `src/templates/app/components/`:
   - Maintain expected `hx-target`/`hx-swap` behavior.
   - Avoid adding JS that duplicates existing HTMX behavior.
3. CSS source of truth:
   - Edit `src/static/css/input.css`
   - `src/static/css/main.css` is build output.

---

## 9) Social Product Defaults (Maintainer Decisions)

These defaults are now agent guidance:

1. Privacy defaults:
   - Public by default for social surfaces.
   - Users can set private accounts.
2. Visibility scope:
   - Profiles, diary, ratings/reviews, lists, and hall of fame are in social scope.
3. Follow model:
   - One-way follow graph.
   - Private accounts require follow approval.
4. Block behavior:
   - Blocked users cannot view blocker profile content or follow.
5. Interactions:
   - Likes only for now.
   - No social comments in current phase.
6. Moderation/control:
   - Content edit/delete by author and project owner/admin only.
   - No full moderation-role system required yet.
7. Spoilers:
   - Users can mark reviews/log content as spoilers.
8. Rollout scope:
   - Social features should apply to all media types together (not phased by type).
9. Feed scope/order:
   - Include diary logs, ratings, and list activity.
   - Reverse-chronological ordering (no algorithmic ranking yet).
10. Social notifications:
    - Disabled for now (no follow/like/comment notification work unless requested later).
11. Performance expectation:
    - Prioritize fast pages and query hygiene; no hard SLA currently defined.
12. Rate limiting:
    - No product-level social action limits required for now unless abuse appears.
13. Audit logging:
    - Log key social actions (follow/unfollow, follow request accept/reject, block/unblock, likes, review/list visibility changes, and privileged deletions).

Implementation preference:
- Keep tracking foundations stable while adding social layers.
- Reuse existing history/signals patterns where sensible.
- Avoid tight coupling between provider ingestion and social graph/feed logic.

---

## 10) Architecture & Delivery Preferences

1. Feature flags:
   - Not mandatory by default.
   - Use feature flags only when explicitly requested or when risk is high.
2. API strategy:
   - Continue server-rendered + HTMX-first delivery.
   - Add dedicated APIs only when a task explicitly needs them.
3. Backward compatibility:
   - Prefer preserving existing URLs and integration behavior.
   - If breaking changes are unavoidable, include explicit migration/redirect notes.
4. Deployment guidance:
   - Docker is supported but optional for contributors.
   - Local dev commands from `LOCAL_SETUP.md` and `FRONTEND.md` are primary references.
5. Documentation precedence:
   - If setup instructions conflict across docs, treat `LOCAL_SETUP.md` as the source of truth.

---

## 11) Change Scope Expectations for Agents

Every non-trivial change should include:

1. What changed
2. Why it changed
3. Risk points
4. Test coverage added/updated (or what remains untested)
5. Any migration/data impact

If uncertain about business behavior, stop and ask instead of assuming.

---

## 12) Remaining Clarification Points (Non-Blocking)

These do not block current implementation but should be finalized later:

1. Long-term deployment platform choice for publicly hosted Spine.

---

## 13) Feature Workflow

Every feature follows this path. Armaan drives it with the prompts in quotes.

1. **Start.** "New feature: <what>. New worktree and branch." Work only in that worktree, under `.claude/worktrees/<name>`: inside the repo, so the settings find the main checkout's `.env`. Keep API changes additive. Don't merge or push.
2. **Build.** Implement the feature and verify it. Run `scripts/check.sh` before calling the work done.
   - New logic gets a test. A bug fix gets a test that fails before the fix and passes after it.
   - Tests must pass with no `.env` and no API keys, as in CI.
3. **Try on the phone.** "Test this on my iPhone." Use the `spine-test-on-iphone` skill (`scripts/ios/TEST_ON_IPHONE.md`). Backend changes run from the branch's own backend on the Mac (`scripts/ios/local-backend.sh`, then `scripts/ios/build-to-iphone.sh --local`); never deploy to test. Repeat 2–3 until Armaan is happy.
4. **Ship.** "Ship this." Use the `spine-ship` skill (`scripts/ios/SHIP.md`): verify, merge into `myapp-main` in the main checkout, and push. CI then deploys production, and Xcode Cloud makes a TestFlight build if the iOS app changed. Watch both, verify production, and remove the worktree.
5. **Roll back.** "Roll back" if a ship broke production (`scripts/ios/SHIP.md`, Workflow B).

## 14) Shipping: Pushing `myapp-main` Goes Live

- Every push to `myapp-main` runs CI (section 15). When CI passes and the push changes what the backend is built from, the "Deploy production" job deploys that exact commit on this Mac: it backs up the database, rebuilds the stack from `~/projects/spine-deploy`, and verifies it. Migrations run as the new container starts.
- If the push changes the iOS app (`ios/Spine/Spine/`, `ios/Spine/Spine.xcodeproj/` or `ios/Spine/Configs/`), Xcode Cloud archives `Spine` and uploads a TestFlight build. Never write a CI skip marker in a commit message: GitHub Actions honors it and would skip CI and the deploy.
- Merge into `myapp-main` only work that is finished, tested, and safe to run in production. Push only when asked ("ship this" counts).
- `myapp-main` can't be force-pushed or deleted (GitHub ruleset). Deploy another branch or an older commit only by hand, on request: `gh workflow run deploy-backend-desk-mac.yml -f branch=<branch> [-f ref=<sha>]`.
- The backend must keep working for installed builds. Additions are safe; removals and renames need expand and contract (`scripts/ios/SHIP.md`).

## 15) CI

- `.github/workflows/ci.yml` runs on every push to any branch, on GitHub-hosted runners (free for this public repo):
  - "Fast checks": `scripts/check.sh` (ruff, `makemigrations --check`, the full suite on SQLite with `uv.lock`).
  - "Database checks": every migration, then the full suite, on Postgres 16 like production.
  - "Deploy production": pushes to `myapp-main` only, after both pass (`deploy-backend-desk-mac.yml`).
- Upstream's App Tests and Stale workflows are disabled in GitHub. Leave upstream's workflow files unedited so upstream merges stay clean.
- When CI goes red, find the run with `gh run list`, read the failure with `gh run view <run-id> --log-failed`, reproduce it (`scripts/check.sh`, or the Database checks commands against a throwaway Postgres), fix the cause, and push. Keep `myapp-main` green.
- Never make a check pass by weakening it: no skipping or deleting a failing test, and no `continue-on-error`. If a check itself is wrong, change it on purpose in its own commit.
- `src/api/tests/test_deploy_workflow.py` pins the deploy workflow and script; change them together.
- The production Mac is a self-hosted runner for this public repo. Workflow runs from fork pull requests wait for Armaan's approval (repo setting); never approve one without reading it. Never send a self-hosted job a `pull_request` or `pull_request_target` event.

## 16) Databases and Production

- Production runs in Docker on this Mac: `spine` (published on `127.0.0.1:8000`; the Cloudflare tunnel serves it as `api.spine-api.com`), `spine-db` (Postgres 16) and `spine-redis`, in Compose project `spine`, built from `~/projects/spine-deploy`. Postgres and Redis aren't published to the host.
- Local development never connects to production's database. Every checkout uses its own SQLite file (`src/db/db.sqlite3`, empty in a new worktree) and the Homebrew Redis on 6379.
- Never point a dev server, build, or script at port 8000: it is production. The iOS app talks to production unless a Debug build is told otherwise (`ios/AGENTS.md`).
- Don't run `docker compose` in `~/projects/spine` or a worktree: production's Compose project is also named `spine`. The main checkout's `.env` sets `COMPOSE_PROJECT_NAME=spine-dev` as a guard. Never stop, recreate, or `down` the production containers by hand.
- `.env.production` holds production's secrets. It lives in the main checkout and is symlinked into `~/projects/spine-deploy`, so editing it changes production at the next deploy. Never print or edit it.
- These touch production on purpose; run them only on request: the deploy workflow, `scripts/backup-production.sh`, `scripts/external-ratings-production.sh`, `scripts/restore-check.sh` (it reads one user count), and the "External Ratings Production Backfill" and "Sync Featured Lists to Production" workflows.
- Dev Redis DB 0 and DB 5 both hold old Celery backlogs in the unprefixed `celery` queue. Never run a worker against DB 0. A worker on DB 5 needs its own `REDIS_PREFIX`, which `scripts/ios/local-backend.sh` sets.
- Never copy the main checkout's dev database (it holds personal data) or production's.

## 17) App Store Connect

- Spine's bundle ID `com.armaan.Spine` is registered (`K8A623RZVV`). The App Store Connect app record and Xcode Cloud aren't set up yet: until this section lists the app ID and the "Me" group ID, skip the TestFlight steps in `scripts/ios/SHIP.md` and say so.
- `node ~/.appstoreconnect/asc.mjs` reads Pylon's app by default; `ASC_APP_ID=<id>` points it at another app. `status` and `get` are reads. `post` and `patch` write: ask first, except where `scripts/ios/SHIP.md` says the ship request covers it.
- The tool signs requests with the API key in `~/.appstoreconnect/private_keys/`. Never copy, print, or commit that key.
- Xcode Cloud assigns build numbers when it uploads. Tag the commit behind each TestFlight build, for example `ios-1.0-build3`.
