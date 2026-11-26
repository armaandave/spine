# Spine World

A web page that lays out everything Armaan has logged in Spine (990 titles, 1,088 logs since 2021) as his own posters, in three modes:

| key | mode | ground | what it is |
|---|---|---|---|
| 1 | **Burst** (`#burst`) | paper | A port of his personal page (on his laptop at `~/projects/playground/mysite/orbit/`, not in the repo). One ray per taste cluster (16), built from posters, oldest at the rim and newest at the tip. Click a ray to unfold it into a filmstrip. His profile sits in the centre. |
| 2 | **Orbit** (`#orbit`) | black | A tilted disc of posters in taste sectors, newest crowding the centre. Click a poster to bring it forward with its 5 nearest titles. |
| 3 | **Tunnel** (`#tunnel`) | black | A corridor of posters through time, newest near you. The wheel flies you back through the years. |

Switching modes flies every poster from one formation to the next.

**The design rule** (DESIGN.md): only posters and small typography at the screen edges. Posters are flat. No lines, rings, glows, labels inside the formation, 3D thickness, tilt or camera sway. Clicking a title re-arranges posters; it never opens a panel.

## Run it

```sh
cd world
npm install
npm run dev          # http://127.0.0.1:5199 (strict port; stop the other one if it's taken)
npx tsc -p .         # type-check (no test suite)
npm run build        # dist/ works from any folder (vite base "./")
```

`public/data` is a symlink to `~/projects/spine-world/site-data` on HomeMac: about 127 MB of `world.json`, poster atlases and full-size posters. It is not in git, because the poster artwork is copyrighted and the repo is public. Elsewhere, copy that folder to `world/public/data/`.

## Data (`public/data/`)

- `world.json`: the contract is `WorldData` in `src/engine/types.ts`.
  - `nodes`: 7,875 titles; his 990 come first (`id` 0–989, `mine: true`).
  - Each node has `title`, `year`, `type`, `maker`, `rating` (0–10), `liked`, `logs`, `dates` (each log `YYYY-MM-DD`), `cluster`, `color`, `next` (5 nearest titles in all of Spine), `near` (≤ 8 nearest among his), `xy` (2D taste map), `sphere` (3D taste position), and its atlas cell.
  - `clusters`: 16 named taste clusters (`name`, `color`, `members`).
- `atlas/s*.jpg`: every poster at 96×144 (albums 96×96 at the top of the cell). `atlas/h*.jpg`: his titles at 240×360.
- `hero/<id>.jpg`: his titles at full size (500×750; albums 600×600).

The similarity data behind `next`, `near`, `cluster` and `sphere` came from the embedding test (Voyage vectors over Haiku-written "feel" descriptions). `data-tools/` holds the one-off scripts that built `world.json`. They read inputs that live only on HomeMac (`~/projects/spine-similarity-data`, a read-only export of his logs), so treat `world.json` as the source of truth.

## Code map

```
src/main.ts               boot: fetch data/world.json → engine → intro → first mode
src/engine/types.ts       THE CONTRACT between engine and modes (Mode, ModeContext, PosterField…); only add to it
src/engine/engine.ts      renderer, scene, mode switching, open/close, window.spine
src/engine/posters.ts     all 990 posters as one GPU mesh: morph between poses, hi-res/hero texture pool, ghosts
src/engine/captions.ts    ctx.captions: DOM captions (title · year · maker · ★ · logs) pinned to posters
src/engine/rig.ts         camera spring, flyTo, orbit controls
src/engine/post.ts        post-processing (everything off by default; bypassed when off)
src/ui/ui.ts              loader, mode index (01 BURST · 02 ORBIT · 03 TUNNEL), hover label
src/modes/burst.ts        2D canvas port of his page (draws its own layer and hides the poster mesh)
src/modes/orbit.ts        the disc
src/modes/tunnel.ts       the corridor
```

A mode is an object with `enter / update / focus / unfocus / exit` (and optional `pointer / wheel / key / resize`). It places posters by writing a `PoseBuffer` and calling `ctx.posters.morphTo()` or `follow()`. To add a mode, write `src/modes/<id>.ts` and add it to the `modes` array in `src/main.ts`.

## Driving it from outside (an iOS web view, tests)

Once the first mode has settled, the page exposes `window.spine`:

```js
spine.switchTo("tunnel")        // "burst" | "orbit" | "tunnel"
spine.open(id)                  // focus a title by node id (his titles are 0–989)
spine.close()                   // back to the formation
spine.ctx.world                 // the loaded world.json
spine.ctx.focused               // focused node id or null
```

Hash routes: `#burst`, `#orbit` and `#tunnel` open a mode directly and skip the intro. A bare URL plays the intro.

For a `WKWebView`, bundle a `dist/` build (it uses relative paths) together with `data/`, or serve it, then call these through `evaluateJavaScript`. Nothing posts events back to native yet. To add that, have `engine.ts` call `window.webkit?.messageHandlers?.spine?.postMessage(...)` on open, close and switch.

The data is hard-coded to Armaan's export. Showing any user's world needs a backend endpoint that builds `world.json` per user. That doesn't exist yet.

## Checking your work visually

`tools/capture.mjs` drives a GPU Chrome (Chrome for Testing in `~/Library/Caches/ms-playwright`) and prints fps and console errors. Only one capture runs at a time; it takes a lock.

```sh
node tools/capture.mjs shot tunnel /tmp/t.png 1440 900 '[{"wait":3500}]'
node tools/capture.mjs video orbit /tmp/o.gif 1280 800 '[{"wait":2600},{"eval":"spine.open(650)"},{"wait":3000},{"key":"Escape"}]' 10 12
CAPTURE_BASE=http://127.0.0.1:5198/ node tools/capture.mjs shot burst ...   # against a build
```

`tools/publish-stable.sh [dir]` builds a frozen copy. `tools/serve-stable.mjs` serves it (`ROOT`, `PORT`). `tools/demo-round.sh` records every mode, and `tools/shrink-gif.py` makes chat-sized `.webp` copies. All three modes hold about 120 fps at 1440×900 on the M1 Pro.

## Known gaps

- Phone layouts exist but were only checked in captures, not on a phone.
- Burst's `#ray-<id>` deep link only works on a fresh load.
- The hover label and captions use Host Grotesk and Fragment Mono from Google Fonts, so they need a network connection.
