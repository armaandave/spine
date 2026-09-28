# Game tracking implementation plan

## Base and isolation

- Worktree: `/Users/armaandave/.codex/worktrees/game-tracking-end-to-end/spine`.
- Branch: `codex/game-tracking-end-to-end`.
- Base: `95b578dfc23b4b0dced3ff35b60ae599f2fdec81` (`myapp-main`).
- Fetched `origin`; local and remote mainline match (0 ahead, 0 behind).
- Preserved all three approved contracts byte for byte. Only the game contract was untracked.
- Leave the original checkout and its unrelated edits untouched.
- No deployment, push, merge, or pull request.

## Implementation order

1. Map all GM laws and G-01–G-35 in `game-tracking-coverage.md`. Trace shared and alternate mutation paths.
2. Add focused failing domain/API and Swift tests. Keep book and single-weight behavior covered.
3. Extend the book lifecycle pattern for game playthroughs. Reuse existing diary, opinion, calendar, activity, and transaction helpers.
4. Add nullable minute totals and whole percentages, independent field provenance, completion snapshots, valid restoration, and date-ordered completed progress selection.
5. Route API, diary, legacy web/form, and import mutations through authoritative domain rules. Preserve async calls and privacy.
6. Wire native detail, rail, menus, progress/completion editors, Play History, library, diary, and profile refreshes.
7. Run backend regression, lint, system, migration consistency, fresh-install, and representative upgrade checks. Run Swift tests and a clean Simulator build.
8. Run an isolated local backend. Launch the new-worktree app with a runtime-only local API override. Exercise the acceptance UI on the configured iPhone and verify the live mirror.
9. Review the complete contract independently. Fix findings and repeat affected tests and UI flows.
10. Record exact results and remaining limits. Commit the contract, implementation, tests, and verification report.

## Shared interface and ownership

- Backend agent owns `src/app`, `src/api`, models, migrations, domain/API tests, and legacy mutation integration.
- iOS agent owns `ios/` and Swift tests.
- Contract/import agent owns `src/integrations`, game import reconciliation, and the coverage matrix. It performs a later contract review.
- Root owns integration, local runtime fixtures, Simulator interaction, final review, evidence, and commit.
- Coordinate shared API fields before either side locks models. Root resolves interface mismatches.

The API adds game state to existing tracking/media responses. It exposes current playthrough, Play History, the undated fact, replay/count state, allowed actions, and separate imported lifetime minutes. Existing status wire values remain unchanged. New game routes follow the book route pattern: progress, completion, playthrough mutation, and status actions. Progress writes distinguish omitted fields, explicit null, and zero. Completion and restart accept stable mutation IDs. Diary responses retain a game completion snapshot separate from current progress.

## Risks and checks

- Restoration must skip deleted evidence, including prior undated completion and prior playthrough references.
- Completed display selection follows completion dates; undo identity follows the controlling status action.
- Progress sources are independent per field. A date-only log edit cannot reconnect them.
- Migration must preserve legacy rows and ambiguous progress without inventing attempts.
- Steam refresh must preserve status and playthrough values. Import overwrite cannot cascade-delete history.
- Every alternate mutation must enforce completion, removal, ownership, and opinion rules.
- A test pass or build pass does not establish Simulator verification. Record only actions actually performed.

## Runtime verification

Use isolated local SQLite data and disposable records. Store local credentials, tokens, configuration, and generated evidence outside source control. Keep the production API default unchanged. Use the configured iPhone 17, UDID `7990CE55-B413-4CCC-8F92-45FD2A33CF32`, and pin the browser mirror to that device. Automated provider fixtures are distinct from live app-to-Django verification.
