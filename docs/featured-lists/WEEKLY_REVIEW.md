# Featured lists: weekly review

The instructions for the scheduled agent that keeps Spine's featured lists healthy. It runs once a week (and can be run by hand: "run the featured lists review"). It proposes changes on a branch and writes a short report. It never touches production. Armaan reviews the report and says "ship this" to deploy.

Read `README.md`, `ADDING_SOURCES.md` and `LIVE_AWARDS.md` in this folder and the repo's `AGENTS.md` first. Every rule there applies.

## 1. Read the health report

Production serves the report at `https://api.spine-api.com/api/v1/lists/featured/health/`. It needs the report token, which lives in the Mac's Keychain. Never print the token or the response headers.

```sh
token="$(security find-generic-password -a "$USER" -s spine-featured-report-token -w)"
curl -sf -H "X-Spine-Report-Token: $token" https://api.spine-api.com/api/v1/lists/featured/health/ > /tmp/featured-health.json
unset token
```

The report has a summary and one row per list: status, last success, last error, counts, resolve rate, `unresolved` entries with their `entry_key`, deferrals, provider budgets, upcoming live windows, orphans and problems.

## 2. Start a branch

Create a worktree and branch named for the day: `.claude/worktrees/featured-review-YYYY-MM-DD` on `worktree-featured-review-YYYY-MM-DD`, from the latest `myapp-main`. Migrate its SQLite database and create the `Spine` user (`ADDING_SOURCES.md`, "Running one list locally").

## 3. Work through the report

1. **Failing lists** (`failed` three or more times, or stale): reproduce with `sync_featured_list <key> --dry-run`.
   - **Source changed shape:** fix the adapter, with a fixture and a test.
   - **Source gone for good:** set `featured=False` on its definition and say why in the report.
2. **Guarded lists:** read the guard message. A real drop at the source (a shorter chart) needs a definition change. A partial fetch needs an adapter fix.
3. **Unresolved entries:** for each, look the title up on the provider with the dev keys. When the match is certain (title, year and creator agree), add a row to that family's `overrides/<family>.csv`: `scope,entry_key,ref,note`. Use `-` to drop entries that don't belong (stand-up specials on a TV chart, box sets). Never guess.
4. **Deferred lists:** nothing to do unless they've been deferred for more than a week. Then check the provider budgets in the report and suggest a budget change.
5. **Letterboxd lists waiting on MDBList:** list the URLs Armaan still needs to add as MDBList external lists.

## 4. Award nights for the next 60 days

`LIVE_AWARDS.md` has the calendar and the steps.

- **Nominations out:** when a ceremony's nominations are out, fill `static/nominees/<ceremony>.csv`, matching every nominee to a ref, then set `featured=True` on its definition. Verify the window's date and time against an official source.
- **After a ceremony:** confirm the winners published. Add next year's definition with an empty nominee file and `featured=False`. Remove the finished ceremony's definition once its all-time list has the winner; the dispatcher then retires its list.

## 5. New editions

- **Steam Awards:** a new year's page appears around January. Add the year to `STEAM_AWARDS_YEARS`.
- **Canon lists with new editions:** TSPDT (January), Sight and Sound (2032), 1001 Movies, Edge and IGN. Re-curate the static CSV and say what changed.
- **Yearly curator picks:** add a new static list (Obama's favourites in late December, for example), and retire or keep last year's.
- **Self-updating:** Kono Manga ga Sugoi!, Goodreads Choice, Pitchfork and Needle Drop year-end lists, Grammys and Billboard update themselves. Check they did.

## 6. Shelf order

If a new list deserves a place near the front of its shelf, add its key to `src/lists/featured/order.py`. Keep "Now" to flagship charts.

## 7. Verify and report

1. Dry-run every list you touched, then run `scripts/check.sh` from the worktree.
2. Commit in logical steps, with messages ending in the `Co-Authored-By` trailer the repo uses.
3. Don't push or merge.

Finish with a short report:

- **Health:** counts by status, and anything still broken.
- **What changed:** the branch and its commits, in one line each.
- **What Armaan needs to do:** Letterboxd links to add, any key or budget change, "ship this" if the branch should go live.
- **Coming up:** the next award nights and what they need.

If nothing needs changing, say so and delete the empty worktree.
