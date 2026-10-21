---
name: spine-ship
description: >-
  Ship Spine work to production and TestFlight: merge a finished feature into
  myapp-main and push it, which deploys the backend once CI passes and makes a
  TestFlight build when the iOS app changed; or roll back a bad ship. Use when the
  user says "ship this", "ship it", "merge and ship", or "roll back". Testing on the
  iPhone first is spine-test-on-iphone.
---

**Required first step:** read `scripts/ios/SHIP.md` (repo root) in full and follow it.
It is the single source of truth for this workflow; this file is only a pointer. If the
current checkout doesn't have that file yet, read `~/projects/spine/scripts/ios/SHIP.md`
instead.

Core rules, in case of doubt:

1. A push to `myapp-main` runs CI and, once it passes, deploys production. It also makes
   a TestFlight build when the push changes the iOS app. Never put a CI skip marker in a
   commit message: it would skip CI and the deploy.
2. Run `scripts/check.sh` before merging, then watch CI and the deploy on the pushed
   commit. A red deploy can mean production is down: fix it or roll back.
3. Never push, deploy, or write to App Store Connect without the user's explicit yes in
   this conversation for this ship.
4. The API must keep working for every installed build.
