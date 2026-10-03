# AGENTS.md — Spine iOS

AI agent operating instructions for the native iOS client. Read this file when working under `ios/`.

Status: Advisory v1.  
Parent context: repository root `AGENTS.md` (Django backend). This file governs **iOS only**.

---

## 1) Product intent

Spine iOS is a **native SwiftUI client** for the Spine media tracker — one app for movies, TV, anime, manga, games, and books, with a Letterboxd-style UX (diary, ratings, lists, profiles, social feed over time).

**Current maintainer direction:**

- **iOS-first delivery** — web UI in `src/` is not the focus; this app is the primary user surface.
- **Backend is Django on a home server** (Docker) today; the **REST API** in `src/` is the data source for the app.
- **Live API only** — the app always talks to the Django API; there is no offline/fake data layer in the client.

Reference: `spine-prd.md` (product direction; PRD still says web-first — iOS supersedes that for this repo).

---

## 2) Tech stack


| Layer               | Choice                                                                 |
| ------------------- | ---------------------------------------------------------------------- |
| Language            | Swift 5.9+                                                             |
| UI                  | SwiftUI                                                                |
| Minimum iOS         | **iOS 17** (unless user requests lower)                                |
| Architecture        | **MVVM** + **repository protocol** (live API implementations)          |
| Networking          | `URLSession` + `Codable` — no third-party HTTP client unless requested   |
| Images              | `AsyncImage` first; Kingfisher/Nuke only if needed                     |
| Persistence (later) | Keychain (tokens) → optional SwiftData cache                           |
| Dependencies        | **Swift Package Manager only** — no CocoaPods                          |


**Do not embed** TMDB/MAL/IGDB keys in the app. All metadata goes through the Django API.

---

## 3) Repository layout

```
ios/
├── AGENTS.md                 # this file
├── Spine/
│   ├── Spine.xcodeproj
│   └── Spine/
│       ├── App/
│       │   └── SpineApp.swift
    ├── Core/
    │   ├── Models/           # Codable types — mirror API JSON shape
    │   ├── Networking/       # APIClient, Endpoints, AuthTokenStore
    │   ├── Design/           # colors, spacing, typography (optional)
    │   └── Environment/      # AppEnvironment, AppConfig
    ├── Features/
    │   ├── Auth/
    │   ├── Search/
    │   ├── MediaDetail/
    │   ├── Tracking/
    │   ├── Diary/
    │   ├── Profile/
    │   ├── Lists/
    │   └── Feed/             # after social API exists
    └── Resources/
```

**Rules:**

- Feature folders own their Views + ViewModels; shared types live in `Core/`.
- No Django/Python under `ios/`.
- Do not modify `src/` when doing iOS-only work unless the task explicitly requires API changes.

---

## 4) Architecture rules

### 4.1 Repository pattern (required)

```swift
protocol MediaRepository {
    func search(query: String, mediaType: MediaType?) async throws -> [MediaSummary]
    func detail(id: Int, mediaType: MediaType) async throws -> MediaDetail
}

struct APIMediaRepository: MediaRepository { ... }
```

- Views and ViewModels depend on **protocols**, not concrete API types.
- Inject repositories via environment or initializer (avoid singletons except `AppEnvironment`).
- Unit tests may use small in-test fakes; do not add app-shipped fake data layers.

### 4.2 App environment

```swift
enum AppEnvironment {
    static var apiClient: APIClient { ... }
}
```

- `AppRepositories.current()` always wires live API repositories.
- API base URL comes from `AppConfig` / `SPINE_API_BASE_URL`.

### 4.3 ViewModels

- `@MainActor` ViewModels exposing `@Observable` or `ObservableObject` state.
- Async work in `task` / explicit `async` methods — no blocking main thread.
- Surface `loading`, `error`, and `empty` states on every list/detail screen.

### 4.4 Navigation

- `TabView` for primary tabs (v1 suggestion): **Search**, **Diary**, **Profile** (Feed later).
- `NavigationStack` within each tab.
- Deep links to media detail: `/media/{type}/{id}` — match future API paths where possible.

---

## 5) Backend contract (API)

Design iOS models against the v1 contract; adjust when `docs/api/` or OpenAPI lands.

### 5.1 Planned base URL

- Default and release: `https://api.spine-api.com` (`AppConfig.productionAPIBaseURL`). Any launch without `SPINE_API_BASE_URL` uses production.
- Local dev: `http://127.0.0.1:8001` from `./start-dev.sh`, Simulator only, opted into per launch (see "Local backend (Simulator)" under Run). On the server Mac, `127.0.0.1:8000` is the production Docker stack, not a dev server.
- Config via `Info.plist` or xcconfig — **never hardcode secrets**.

### 5.2 v1 endpoints (target)


| Area     | Methods                                      | Notes                          |
| -------- | -------------------------------------------- | ------------------------------ |
| Auth     | `POST /api/v1/auth/login`, refresh, register | JWT access + refresh           |
| Search   | `GET /api/v1/media/search`                   | Query + `media_type`           |
| Media    | `GET /api/v1/media/{type}/{id}`              | Metadata + user tracking state |
| Tracking | `PUT /api/v1/tracking/{type}/{id}`           | Status, rating, progress       |
| Diary    | CRUD `/api/v1/diary/`                        |                                |
| Profile  | `GET/PATCH /api/v1/me/`                      |                                |
| Lists    | CRUD `/api/v1/lists/`                        | after core tracking            |


### 5.3 Auth

- Store refresh token in **Keychain**.
- Attach `Authorization: Bearer …` on API requests.
- **Sign in with Apple** required before App Store if other social logins exist — plan for it, implement when auth API exists.

### 5.4 Error handling

- Map HTTP status to user-visible messages.
- 401 → clear tokens, return to login.
- Network offline → show error UI; do not silently substitute fake data.

---

## 6) MVP scope (iOS v1)

**In scope:**

1. Auth screens wired to API
2. Search (multi-type or type filter)
3. Media detail (poster, metadata, user status)
4. Track / rate / progress update
5. Diary list + log entry
6. Profile (self) — stats, recent activity placeholder
7. Settings — base URL for dev, about/attribution (TMDB etc.)

**Out of scope for v1:**

- Push notifications
- Social feed, follow, likes (UI stubs OK, not required functional)
- Offline sync queue
- iPad-optimized layouts (iPhone first)
- Widgets, Share extension
- tvOS / macOS

---

## 7) Design direction

- **Media-first** — posters/covers are the visual anchor; minimal chrome.
- **Letterboxd familiarity** — diary + star ratings + clean typography; extended to all media types.
- **Dark mode** — support both; prefer system appearance.
- **Accessibility** — Dynamic Type, VoiceOver labels on posters and actions.

Use SF Symbols unless custom assets are provided. No web/Tailwind parity required — native iOS feel over pixel-perfect web match.

---

## 8) ShipSwift / SwiftUI skills

For SwiftUI UI work, consider the installed ShipSwift skills:

- `add-component` for reusable UI components, loading states, empty states, cards, buttons, forms, charts, and visual polish.
- `build-feature` for larger UI features such as onboarding, settings, camera, chat, paywall, auth, or multi-screen flows.
- `explore-recipes` when looking for relevant ShipSwift recipes before implementing.

Use these skills only when directly relevant to the requested SwiftUI UI change.

Rules:

- Do not use ShipSwift for backend, model, data, networking, test, build-system, or bug-fix work unless the task is specifically about SwiftUI UI.
- Prefer the smallest relevant recipe/component.
- Do not introduce broad ShipSwift architecture, dependencies, naming conventions, or modules unless explicitly requested.
- Keep the result consistent with the existing Pylon design system and file structure.
- Keep diffs minimal.

---

## 9) Non-negotiable rules

1. **No API keys** in the iOS repo (TMDB, MAL, IGDB, etc.).
2. **No AGPL backend code** copied into Swift — client is a separate work; keep it a thin HTTP client.
3. **No secrets in git** — base URLs for dev OK; tokens never committed.
4. **HTTPS only** for non-local builds (App Transport Security); tunnel URL is fine for dev.
5. **No app-shipped fake data** — previews and the running app use the live API (or are omitted when the API is unavailable).
6. **Coordinate with backend** — if an endpoint is missing, add it in Django; do not fake it in the client.

---

## 10) Getting started (human + agent)

### Create the Xcode project (once)

1. Xcode → New Project → iOS **App**
2. Product Name: **Spine**, Interface: **SwiftUI**, Language: **Swift**
3. Save into `**spine/ios/`** (creates `ios/Spine.xcodeproj`)
4. Set bundle ID: e.g. `app.spine.ios` (placeholder until domain finalized)

### First implementation order

1. `AppEnvironment` + `APIClient` + `APIMediaRepository`
2. `Core/Models` — `MediaType`, `MediaSummary`, `TrackingStatus`, `DiaryEntry`
3. `Features/Search` — search UI against live API
4. `Features/MediaDetail` — detail from API
5. `Features/Diary` — list from API
6. Tab shell + navigation

### Run

- Open `ios/Spine/Spine.xcodeproj` in Xcode.
- Select iPhone simulator → Run (⌘R).
- API base URL: `ios/Spine/Spine/Core/Environment/AppConfig.swift`
- Scheme: **Debug**. Without an override it talks to production (`https://api.spine-api.com`).

### Local backend (Simulator)

Production is the default on purpose. Use the local backend only when you mean to, and say which backend you used when reporting results.

1. Start the backend from the repo root: `./start-dev.sh` (Django on `http://127.0.0.1:8001`; set `SPINE_DEV_PORT` to change the port). Check `curl http://127.0.0.1:8001/api/v1/health/` returns 200.
2. Launch with `SPINE_API_BASE_URL` set:
   - simctl: `SIMCTL_CHILD_SPINE_API_BASE_URL=http://127.0.0.1:8001 xcrun simctl launch --terminate-running-process <UDID> com.armaan.Spine`
   - XcodeBuildMCP: `build_run_sim` cannot pass launch environment, so it always launches against production. Build (or build and run), then `stop_app_sim`, then `launch_app_sim` with `env: {"SPINE_API_BASE_URL": "http://127.0.0.1:8001"}`.
   - Xcode: duplicate the scheme with **Shared** unchecked and set the variable in its Run action. Never add it to the shared `Spine.xcscheme`.
3. Confirm the app's requests show up in the `runserver` output.

Never point a "local" build at `127.0.0.1:8000` on the server Mac. That port is production, so the build would read and write production data.

Release builds keep `AppConfig.productionAPIBaseURL`. Installed builds have no launch environment, so they always use production; don't change that default to a local URL.

### Codex Desktop simulator mirror

For visual iOS work in Codex Desktop, do **not** stop at a normal XcodeBuildMCP build/run. The expected workflow is:

1. Use XcodeBuildMCP to build and run the app on the configured simulator.
2. Start `serve-sim` for that exact simulator UDID.
3. Open `http://localhost:3200` in the Codex in-app browser.
4. Keep the `serve-sim` process running while visual review or iteration is active.
5. Verify the in-app browser is rendering a real simulator frame before reporting success.

Use this mirror workflow when the user asks to:

- visually inspect the iOS app
- review UI, layout, spacing, typography, colors, or animations
- highlight/select areas for annotations
- view changes live in Codex Desktop
- iterate on SwiftUI screens with visible feedback

Plain build/test requests do not require the mirror. In those cases, use XcodeBuildMCP build/test tools without opening the in-app browser.

Canonical user-facing prompt this repo should support:

```text
Build and run the iOS app, then mirror the iPhone simulator inside the Codex in-app browser with serve-sim. Keep the mirror open while we work.
```

Implementation notes:

- The simulator UDID must come from XcodeBuildMCP session defaults or the build/run result.
- Prefer the configured simulator in `.xcodebuildmcp/config.yaml` unless the user asks for another device.
- If `serve-sim` prints a different local URL, open that exact URL instead of assuming port `3200`.
- If the browser mirror is unavailable or blocked, report that clearly; do not claim visual verification from Apple Simulator alone.
- Normal app changes still require rebuild/relaunch unless using a supported SwiftUI preview hot-reload workflow.

---

## 11) Testing

- **Unit tests:** ViewModels and JSON decoding (`SpineTests/`)
- **UI tests:** defer until core flows stable
- Use inline JSON fixtures in tests for decoder coverage — keep test data in `SpineTests/`, not in the app target

No XCTest required for every change; required for non-trivial decoding/network logic.

---

## 12) Attribution (App Store / About)

About screen must credit third-party data sources when showing their metadata, e.g.:

- “This product uses the TMDB API but is not endorsed or certified by TMDB.”

Match backend/provider requirements; see Django settings for which providers are enabled.

---

## 13) Coordination with backend

When adding an iOS feature that needs data:

1. Check if Django already has the domain logic in `src/app/` or `src/users/`.
2. If no API exists, **open a backend task** to add DRF endpoint — don’t call Django HTML views from iOS.
3. Propose JSON shape in `docs/api/` (create when first endpoint is added) before locking Swift models.

Agent working **only on iOS** should note API gaps in the PR/summary, not silently invent undocumented endpoints.

---

## 14) Change handoff template

When completing non-trivial iOS work, summarize:

1. What changed (screens, flows)
2. API endpoints used or still needed
3. Backend dependencies
4. How to run/preview (including API availability)
5. Known limitations
