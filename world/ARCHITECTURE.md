# Spine World — architecture and rules of the road

> Written when the site had five modes and a team of agents. README.md is the current guide;
> the engine contract (src/engine/types.ts) and the stack notes below still hold.

## Stack

Vite + TypeScript + three.js r187 (WebGL2) + gsap + postprocessing (pmndrs) +
troika-three-text. Vanilla three, no React. Libraries may be added with `npm i` ONLY by
the engine agent (ask it via your report if you need one). Dev server already running:
http://127.0.0.1:5199/#<mode> (HMR on). Armaan views it at https://homemac.tail78f3fa.ts.net:8446.

## Files and owners (never edit a file you don't own)

```
src/engine/types.ts     THE CONTRACT (engine agent may ADD only; never rename/remove)
src/engine/*.ts         engine agent (renderer, posters, camera rig, input, post, transitions)
src/ui/*, src/style.css engine agent (panel, nav, intro, hover label)
src/main.ts, index.html engine agent
src/modes/<id>.ts       one mode agent each (+ optional src/modes/<id>/ folder)
src/modes/_flat.ts      reference stub (read only)
DESIGN.md               creative director
TECH.md                 tech researcher
feedback/<id>.md        curator writes, mode agents read
demos/                  curator
tools/capture.mjs       read only
public/data             read only (world.json + poster atlases)
```

## The contract (src/engine/types.ts — read it fully)

- The engine owns ONE PosterField: his 990 posters as an instanced mesh. Slot i =
  world.nodes[i] (his titles are ids 0..989). Modes never create their own copies of his
  posters; they give the field target poses (`morphTo` for transitions, `follow` for
  per-frame motion like spinning). This is what makes mode switches a single morph.
- Titles he hasn't logged appear only as "ghosts" (`posters.ghost(id, at)`), e.g. the
  `next` of a focused title.
- A mode adds its own environment (lines, rings, tunnel walls, labels, particles) to
  `ctx.group`, drives the camera through `ctx.rig`, and handles pointer/wheel/keys.
- Engine handles: click without drag → pick → `open(id)` / click empty → `close()`;
  hover label; panel; Esc; keys 1–5; resize; render loop; post-processing.
- `window.spine` = { open(id), close(), switchTo(modeId), ctx } for scripted captures.

## Data (public/data/world.json, see TitleNode in types.ts)

7,875 titles; his 990 first. Each has next (5 nearest in all of Spine), near (≤8 nearest
among his), xy (2D taste map), sphere (3D unit vector), cluster (16 named taste clusters,
world.clusters), color (dominant poster color), dates (each log), rating, liked, logs,
feel words, blurb. Atlases: base 96×144 cells for all titles, hi 240×360 for his
(the engine exposes sharp textures for focused posters).

## Seeing your work (mandatory, often)

GPU capture (real Metal GPU, real fps; serialized with a lock — keep runs short):
```
cd /Users/armaandave/projects/spine-world-3d
node tools/capture.mjs shot  <mode> demos/<you>-a.png 1440 900 '[{"wait":3000}]'
node tools/capture.mjs video <mode> demos/<you>-a.gif 1440 900 '[{"wait":1500},{"eval":"spine.open(7)"},{"wait":2500},{"key":"Escape"},{"wait":2000}]' 7 20
```
It prints console errors and measured fps. Open PNGs with your image reader. GIFs are for
the curator and Armaan; for your own checking, take several PNG shots mid-motion with
{"shot":"path"} actions instead (cheaper). Never leave a browser or server running.
`npx tsc -p .` must stay clean for your files.

## Process

- Work in passes: build → capture → look hard → fix. Every pass ends with screenshots you
  actually looked at.
- A curator records demos of every mode every ~10 minutes and writes blunt notes to
  feedback/<mode>.md. Read your feedback file at the start of every pass and act on it.
- Follow DESIGN.md (creative director) once it exists; it overrides BRIEF.md where they differ.
- The machine is shared with production: no extra servers, no heavy builds, one capture
  at a time (the lock handles it), keep memory sane.
- Final report: short — what it looks like, how to use it, known limits, best capture paths.


## Engine API additions (engine v1)

All in src/engine/types.ts with comments. Summary:
- `ctx.posters.hovered`, `.sharp(ids)` (focused + nexts get full-res /data/hero/<id>.jpg textures, ids on screen big get the 240 hi atlas automatically), `.light`, `.rimStrength`, `.uniforms` (`uThick`, `uSwirl`, `uOpacityMul`), `.clock()`.
  Posters are opaque boxes: `opacity` < ~0.9 fades towards the ground colour (no holes), < 0.25 dithers away. `dim()` also fades to the ground colour. `scene.fog` (Fog/FogExp2) is honoured by posters.
- `ctx.rig`: critically damped; `parallax` (0..1, default 0.3), `sway`, `flying`, `stop()`, flyTo option `arc`; gsap ease names (default power3.inOut). Direct manipulation (orbit drag) is not smoothed.
- `ctx.controls.orbit({target,distance,azimuth,polar,min/maxDistance,inertia,autoRotate})` -> OrbitControls (`target`, `azimuth`, `polar`, `distance`, `nudge`, `syncFromRig`, `enabled`). The engine routes unused drags + wheel to it. Disposed on mode switch.
- `ctx.post.set({grain,vignette,bloom,dof,aberration}, seconds)`, `ctx.post.focusOn(Vector3|null,{range,bokeh})` (engine calls it on open/close with the focused poster), `ctx.post.bloomThreshold` (1.0: only HDR > 1 colours glow). Engine resets post per mode (grain 0.055 dark / 0.022 light).
- `ctx.layout.focusRect()/focusNdc()/worldAt()/frame(target, distance, dir)`: the screen area free of the panel; `frame` gives rig.flyTo arguments that put a poster centre in that area.
- `ctx.background` (THREE.Color, crossfaded by the engine; set only `Mode.background`).
- Helper: `import { makeCurves } from "../engine/lines"` one-draw-call hairline ribbons (TECH.md section 3). Fades across mode switches use the `uOpacity` uniform (or material.opacity / troika fillOpacity).
- Mode switch: old group is faded out and disposed late (dispose() deferred), new group fades in (1.0-2.2 s), ground colour crossfades; posters morph on the GPU. Keep objects in `ctx.group`; add new ones within ~1 s of enter() to be faded in.
- `window.spine` ({switchTo, open, close, ctx, post, rig}) appears once the first mode has settled.
