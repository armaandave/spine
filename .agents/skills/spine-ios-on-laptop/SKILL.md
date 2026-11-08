---
name: spine-ios-on-laptop
description: >-
  Run Spine's iOS builds, simulators, UI checks, test runs and phone installs on Armaan's
  laptop over Tailscale instead of HomeMac. Use before the first iOS build, simulator or
  test run in any Spine chat (ask "laptop or HomeMac?" once per chat), and whenever the user
  says "use the laptop", "build on my laptop", "run the sim on the laptop", or "use HomeMac".
---

**Required first step:** read `scripts/ios/LAPTOP.md` (repo root) in full and follow it.
It is the single source of truth; this file is only a pointer. If the checkout doesn't
have it yet, read `~/projects/spine/scripts/ios/LAPTOP.md` and run the scripts by path
from the main checkout.

Core rules, in case of doubt:

1. Before the chat's first iOS build, simulator or test run, ask Armaan "Build on the
   laptop or on HomeMac?" and wait. Use his answer for the rest of that chat; follow him
   if he switches. Never pick for him or reuse another chat's answer.
2. Laptop jobs: `scripts/ios/laptop.sh` (run, exec, sim start|app|stop, pull) and
   `scripts/ios/build-to-iphone.sh --laptop`, wrapped in `agent-cleanup run` without
   `--queue ios`. If the laptop is unreachable, tell Armaan and wait; no silent fallback.
3. On the laptop touch only `~/spine-agent` and `Spine Agent *` simulators, open no
   windows, and `sim stop` when done.
