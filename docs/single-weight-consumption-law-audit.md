# Single-Weight Consumption Law Audit

Audited against `docs/single-weight-consumption-contract.md`, approved v1.

The shared domain implementation is `src/app/single_weight.py`. API, web,
imports, and webhooks delegate movie/music mutations to it. Focused proof is in
`src/api/tests/test_single_weight_consumption_contract.py`; the method names
below omit that common path unless another suite is named.

## Core laws

| Law | Implementation evidence | Automated proof |
| --- | --- | --- |
| SW-001 | `mark_consumed`, `_ensure_tracking`, and `_set_last_consumed` establish direct completed tracking without diary/date/activity. | `test_direct_consumption_rating_and_like_are_undated_and_unique`; iOS `testMusicMarkOnlyCompletesWithoutCreatingDiaryEntry` |
| SW-002 | `create_log` creates one `DiaryEntry` and reuses the unique tracking row inside one transaction. | `test_repeat_defaults_duplicates_and_last_date_follow_evidence`; `test_full_movie_music_transition_parity` |
| SW-003 | Tracking creation is shared by `mark_consumed`, `set_rating`, `set_like`, and `create_log`; model constraints allow one row per user/item. | `test_direct_consumption_rating_and_like_are_undated_and_unique`; `test_movie_and_music_api_transitions_have_matching_canonical_state` |
| SW-004 | Direct transitions always clear/leave `end_date=None`; statistics use diary dates rather than undated tracking. | `test_direct_consumption_rating_and_like_are_undated_and_unique`; `api.tests.test_stats` |
| SW-005 | `_set_last_consumed` orders diary evidence by calendar consumption date and recomputes after update/delete. | `test_repeat_defaults_duplicates_and_last_date_follow_evidence` |
| SW-006 | `calendar_datetime`/`calendar_date`, diary serializers/services, web forms, and iOS date-only requests require a local non-future date. | `test_dates_are_mandatory_and_not_future`; `test_api_rejects_missing_future_and_invalid_half_star_dates`; iOS `testSingleWeightCalendarDateFutureValidationAndLanguage` |
| SW-007 | No user/item/date uniqueness exists; import identity has a separate conditional constraint. | `test_repeat_defaults_duplicates_and_last_date_follow_evidence`; Letterboxd `test_import_keeps_multiple_diary_logs_for_same_movie` |
| SW-008 | `unwatch` row-locks state, rejects when logs exist, and otherwise deletes tracking/current like/rating without touching lists. | `test_unwatch_is_blocked_and_final_log_respects_direct_evidence`; `test_unwatch_preserves_custom_list_membership_and_clears_current_state` |
| SW-009 | `delete_log` checks remaining diary/direct evidence, then either preserves/decouples current state or performs normal unwatch cleanup. | `test_unwatch_is_blocked_and_final_log_respects_direct_evidence`; `test_source_heart_edits_and_deletion_preserve_current_without_selecting_older` |
| SW-010 | `default_repeat` derives new-log defaults from tracking/diary evidence; saved `is_rewatch` is not retrospectively recalculated. | `test_repeat_defaults_duplicates_and_last_date_follow_evidence`; `test_import_combines_local_history_orders_same_day_and_trusts_repeat`; iOS `testSingleWeightLogPrefillsCanonicalDraftAndRepeatEvidence` |
| SW-011 | iOS `MediaDetailView` persists consume/like before presenting the optional picker and treats picker dismissal as draft cancellation only. | iOS `testDraftDismissalAndLocalConfirmation`; simulator scenarios 1 and 4 |
| SW-012 | `unwatch` is the single removal invariant used by API, web, and webhooks; legacy tracking writes cannot bypass it. | `test_legacy_tracking_writes_cannot_add_dates_or_downgrade_consumed_state`; blocked-unwatch API assertions in `test_wire_ratings_canonical_state_conflict_social_and_authorization` |

## Rating laws

| Law | Implementation evidence | Automated proof |
| --- | --- | --- |
| SW-100 | Movie/Music have current `score` plus nullable `rating_source`; diary ratings remain on `DiaryEntry`. | `test_rating_couples_decouples_edits_clears_and_deletes_without_rollback` |
| SW-101 | Rated `create_log` writes the diary rating and replaces current rating/source. | `test_rating_couples_decouples_edits_clears_and_deletes_without_rollback` |
| SW-102 | `create_log` leaves current rating untouched when the submitted diary rating is absent. | `test_unrated_and_non_source_logs_preserve_current_rating_and_source_clear_is_scoped` |
| SW-103 | `set_rating` clears current only; `update_log` clears diary/source only when coupled. | `test_unrated_and_non_source_logs_preserve_current_rating_and_source_clear_is_scoped` |
| SW-104 | `update_log` compares the edited row with `rating_source_id` before propagating changes/clears. | `test_rating_couples_decouples_edits_clears_and_deletes_without_rollback`; `test_unrated_and_non_source_logs_preserve_current_rating_and_source_clear_is_scoped` |
| SW-105 | Direct `set_rating(..., direct=True)` establishes the direct marker and clears `rating_source`; diary values are not rewritten. | `test_rating_couples_decouples_edits_clears_and_deletes_without_rollback` |
| SW-106 | `delete_log` preserves a deleted source's current value as independent while other/direct evidence remains and never selects an older row. | `test_rating_couples_decouples_edits_clears_and_deletes_without_rollback` |
| SW-107 | `rating_from_wire` accepts only 0.5 increments from 0.5–5.0; `rating_to_wire` converts doubled storage; zero maps to absent. | `test_api_rejects_missing_future_and_invalid_half_star_dates`; iOS `testSingleWeightRequestsEncodeDateOnlyWireRatingAndExplicitClears` |
| SW-108 | `fill_empty_current_rating` couples an empty current rating to a log whose rating an edit changes; `update_log`, book `update_completion`, and game `update_completion` call it. Migration 0084 fills titles left empty before the law. | `test_rating_an_unrated_log_fills_an_empty_current_rating`; `test_rating_an_imported_unrated_log_shows_on_the_media_page`; `test_rating_fill_migration` |

## Like laws

| Law | Implementation evidence | Automated proof |
| --- | --- | --- |
| SW-200 | `set_like` is atomic and ensures direct tracking before creating `MediaLike`. | `test_direct_like_is_atomic_and_creates_no_feed_activity` |
| SW-201 | Direct unlike removes only `MediaLike`, preserves tracking/direct evidence, and does not edit diary hearts. | `test_heart_couples_including_off_and_direct_change_decouples` |
| SW-202 | `create_log` always stores visible diary `liked` and makes it current, including false, with `like_source` provenance. | `test_heart_couples_including_off_and_direct_change_decouples`; iOS `testSingleWeightLogPrefillsCanonicalDraftAndRepeatEvidence` |
| SW-203 | Direct heart changes set independent provenance; source-only edits follow; source deletion preserves without selecting an older row. | `test_source_heart_edits_and_deletion_preserve_current_without_selecting_older`; `test_heart_couples_including_off_and_direct_change_decouples` |

## Save and cancellation laws

| Law | Implementation evidence | Automated proof |
| --- | --- | --- |
| SW-300 | `create_log`, `update_log`, and `delete_log` use `transaction.atomic` plus row locks for multi-record state. | `test_log_creation_rolls_back_all_state_on_failure`; authorization portion of `test_wire_ratings_canonical_state_conflict_social_and_authorization` |
| SW-301 | iOS composer values are local drafts; close dismisses without repository calls. Web forms mutate only after valid submission. | iOS `testDraftDismissalAndLocalConfirmation`; simulator scenario 6 cancellation |
| SW-302 | `MediaLogView` initializes rating/heart from canonical state and sends both visible values in `DiaryCreateRequest`. | iOS `testSingleWeightLogPrefillsCanonicalDraftAndRepeatEvidence`; backend `test_heart_couples_including_off_and_direct_change_decouples` |
| SW-303 | `DiaryLogDetailView` is the shared diary editor reached from diary/media contexts; backend update/delete services are entry-point neutral. | iOS `testDiaryRepositoryUpdatesEntry`, `testDiaryRepositoryDeletesEntry`; backend source edit/delete tests |

## Social and privacy laws

| Law | Implementation evidence | Automated proof |
| --- | --- | --- |
| SW-400 | Single-weight diary/community/stat views use account visibility; per-entry visibility is not a single-weight trust boundary. | `test_account_privacy_governs_reviews_community_and_single_weight_stats_use_wire_scale` |
| SW-401 | `_create_diary_activity` creates one diary event; `_set_rating_activity` is used only for direct rating; consume/like are silent; diary edits update existing activity. | `test_wire_ratings_canonical_state_conflict_social_and_authorization`; `test_direct_like_is_atomic_and_creates_no_feed_activity` |

## Import laws

| Law | Implementation evidence | Automated proof |
| --- | --- | --- |
| SW-500 | `import_title_state` and `import_logs` share the canonical model and suppress social activity. Import adapters use these paths. | `test_imported_undated_title_state_is_direct_independent_and_silent`; Letterboxd/Trakt/IMDb suites |
| SW-501 | `import_title_state` creates undated direct tracking and independent current rating/like without a diary row. | `test_imported_undated_title_state_is_direct_independent_and_silent`; Letterboxd `test_likes_import_creates_direct_tracking_row` |
| SW-502 | `_reconcile_imported_current_state` preserves independent state and otherwise selects combined history by date, existing-before-imported, then source order. | `test_import_order_idempotency_repeats_and_independent_state`; `test_import_combines_local_history_orders_same_day_and_trusts_repeat` |
| SW-503 | `import_logs` trusts supplied repeat values and derives omitted values in canonical source order using prior direct/local evidence. | `test_import_order_idempotency_repeats_and_independent_state`; `test_import_combines_local_history_orders_same_day_and_trusts_repeat` |
| SW-504 | Diary import identity fields and `app_diary_unique_import_source_record` provide source-record idempotency without date uniqueness. | `test_import_order_idempotency_repeats_and_independent_state`; Letterboxd `test_new_mode_skips_duplicate_diary_and_list_items` |

## Parity and consistency laws

| Law | Implementation evidence | Automated proof |
| --- | --- | --- |
| SW-600 | `supports`, `_tracking_model`, and every domain transition are shared between Movie and Music; iOS varies labels through media-type presentation helpers. | `test_full_movie_music_transition_parity`; `test_movie_and_music_api_transitions_have_matching_canonical_state`; iOS `testMusicPresentationUsesAlbumWordingWithoutChangingWireStatus` |
| SW-700 | Canonical tracking responses expose direct evidence, diary count/date, rating/like provenance and current values. iOS mutation paths reload media/tracking; diary deletion/edit and tab refreshes invalidate affected surfaces. | `test_movie_and_music_api_transitions_have_matching_canonical_state`; iOS repository/view-model tests; all simulator scenario 21 surfaces |

## Final review findings

- **Scope:** `single_weight.supports` is limited to movies and music. Existing
  progress-based tracking services remain the fallback for all other media.
- **Migration safety:** migration 0072 adds nullable/defaulted provenance and
  import fields, deduplicates legacy movie/music tracking before applying
  unique constraints, and derives dates from existing diary history. It has a
  no-op reverse data migration and no destructive contract-breaking operation.
- **Transactions/races:** state-changing domain functions use atomic blocks and
  `select_for_update`; database uniqueness protects tracking and import identity.
- **Timezones:** diary input is normalized as a calendar date and future-checked
  against the user's local date; last-consumed derives from diary dates.
- **Compatibility:** the public single-weight wire scale is 0.5–5.0 while the
  existing doubled storage remains in place. Out-of-scope serializers retain
  their former scale/behavior.
- **UI/accessibility:** movie/music actions share implementation and expose
  explicit accessibility labels, half-star tap targets, adjustable rating
  controls, loading disablement, rollback/error messages, and media-specific
  watched/listened copy.
- **Configuration/security:** localhost is available only through a runtime
  debug environment override. Release configuration continues to use the
  production API URL. No simulator credentials were added to source, fixtures,
  scripts, configuration, or documentation.
- **Unrelated work:** pre-existing Hall of Fame/Profile changes and the
  untracked Excalidraw document were preserved and not attributed to this work.

Validation commands and their final results are recorded in the completion
handoff after the post-audit rerun.
