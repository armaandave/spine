# SPINE WORLD — DESIGN (v3, 2026-11-22)

> **Owner's clarification (read this first, it overrides anything below that contradicts it):**
> "the concept is there the execution is not. i think the tunnel going through time is so
> cool. it would be even better without the lines… clean up the looks."
> So this is a CLEAN-UP, not a re-concept:
> - Every mode keeps its v2 concept, composition and ground colour. Tunnel, globe, orbit and
>   rings stay dark. Bloom and burst are paper. Start from the v2 code (git commit a7ec309)
>   and remove things; don't redesign.
> - Strip every non-poster element: lines, rings, ticks, needles, terminators, dots, glows,
>   lights, dust, in-world labels and panels.
> - Posters fading into the ground colour with depth is allowed and good. The tunnel receding
>   into black is the look. A vignette is allowed only if it is very subtle, and only on dark
>   modes.
> - Clean means fewer things, bigger posters, better spacing and calmer motion.

v2 (DESIGN-v2-retired.md) is dead. It asked for suns, orbital ellipses, spokes, grooves,
needles, terminators, equators, atmospheres, rails, hub ripples, connector lines, in-world
labels and a dark info panel. Armaan's verdict on all of it: "too much structure and clunk…
the posters are the presentation, and the organization of the posters. That is the product.
That is the view. That is the goal. That is the gold." Where BRIEF.md says "posters and
lines", lines are now out. This file overrides BRIEF.md and everything in feedback/v2/.

## The reference: Burst (src/modes/burst.ts, mode 01)

Burst is the one mode he likes. It is a port of his own personal page. Study it before
anything else: run it at http://127.0.0.1:5199/#burst and read ~/projects/playground/mysite/orbit/index.html (his personal page, on his laptop; not in the repo).
What makes it work:
- **The shape IS the posters.** The rays are built only from posters, stacked tile by tile.
  Take the posters away and there is nothing left on screen.
- **The organization is the meaning.** Which ray a poster sits in (taste), and where along
  the ray (oldest at the rim, newest at the tip), is the whole story. No line, axis or label
  is needed to tell it.
- **Text lives at the edges, as editorial typography**, never inside the formation: a kicker,
  a big heading, a one-line note, a numbered legend, a readout. Like a printed plate in a book.
- **Opening something re-arranges posters**: a ray straightens into a filmstrip and the best
  titles unfold into big frames with a caption under each. Opening is a new layout of the
  same posters, never a panel over the scene and never lines drawn between things.
- **Calm paper, ink and real poster colour.** Nothing glows, no effects.
- **Exact, springy motion**: critically damped springs, staggered, nothing snaps.

## The one rule

Only two kinds of things may be on screen:
1. **Posters**: his 990 titles, plus ghost posters for unlogged "next" titles.
2. **Editorial typography at the edges** in Burst's style: Host Grotesk for headings and
   titles, Fragment Mono for small uppercase labels and numbers, ink colours only.

Everything else is banned, in every mode, with no exceptions:
- Circles, rings, ellipses, orbits, grooves, ridges, dial ticks, spokes, rails, grids, axes,
  equators, graticules, atmospheres, terminators or day/night shading.
- Lines of any kind, including "hairline" connectors between titles.
- Dots or hub markers, ripples, dust or particles, stars, glows, suns or points of light.
- Depth-of-field blur, bloom, grain, chromatic aberration. (A depth fade to the ground colour is
  fine; a vignette only very subtle, and only on dark modes.)
- In-world 3D text, labels floating among the posters, or labels pinned to formations.
- The dark focus panel and boxes.

If you are about to add something that is not a poster to "explain" the layout, the layout
is wrong. Change where the posters go instead.

The structure has to come from the poster arrangement: density, gaps, alignment, size,
order, tilt, overlap and stacking. A sphere is a sphere because posters tile it. A timeline
reads as a timeline because posters run in date order. A cluster reads as a cluster because
its posters crowd together with space around it.

## Look (all modes)

- **Ground:** each mode keeps its v2 ground.
  - Paper modes (burst, bloom): #E7E5E0, with inks #141413 / #4A4843 / #86827A.
  - Dark modes (orbit, globe, rings, tunnel): their near-black ground, with light ink #F2EFE8
    at reduced opacities.
  - Burst's type works in both themes.
- **Posters:** real artwork, sharp, never tinted or blurred. A thin box edge is fine. The
  engine's subtle hover lift is fine. Ghost posters (unlogged titles) are at 40–50% opacity.
- **Distance:** in 3D modes, far posters get smaller with perspective. They may fade slightly
  toward paper, but never into a fog wall.
- **Size:** posters must be big enough to recognise. A formation fills the frame
  deliberately, with paper as negative space around it.
- **Chrome:** Burst's corner set and nothing else.
  - Top-left: mode label and date.
  - Top-right: "Fig." line.
  - Bottom-left: mode index (engine).
  - Bottom-right: readout.
  - A mode may add a Burst-style legend or a heading block if its organization needs naming
    (for example, cluster names listed at the edge with numbers). Never inside the formation.

## Opening a title (every mode)

Clicking a poster re-arranges posters, the way Burst does:
- The clicked poster comes forward, big and sharp, in the free area of the screen.
- Its 5 "next" titles arrange themselves beside it, aligned in a row or a short column like
  Burst's unfolded frames. Each one is a poster, or a ghost poster if he hasn't logged it.
- The rest of the formation steps back and quiets down: it shrinks, spreads or dims a little
  toward paper. It does not blur.
- Captions are Burst-style, set under or over each poster by the engine's `ctx.captions`
  (see ARCHITECTURE.md):
  - The title in Host Grotesk.
  - A mono line: "year · maker".
  - A mono line: "★ 4.5 · 3 logs".
  - An index number like "01.03".
- Clicking a next title travels to it. Esc, or clicking empty paper, re-forms the formation.

## Motion

- Keep the engine's morph between modes: posters fly, staggered, on curved paths.
- Everything else uses springs or eases with weight, as in Burst. No snapping, no popping and
  no linear tweens.
- Idle motion is quiet: a slow drift or rotation of the formation itself, never effects.

## Modes (only these three are kept, since 2026-11-22; bloom, globe and rings were cut)

1. **BURST** (paper, done): taste categories as rays, ordered rim to tip in time.
2. **ORBIT** (dark): you are the centre. Your posters revolve around the centre in taste sectors,
   and the more recently you logged a title, the closer it sits. Keep the tilted disc, the slow
   revolve and re-centring on click.
   - Remove: the sun point, the orbital ellipses, the spokes, the star dust, the cluster labels
     in the world.
   - The centre is just where the posters crowd in.
3. **TUNNEL** (dark), the owner's favourite: a corridor of posters running through time, newest
   at you, receding into black. Keep the wheel-flight, the auto-cruise, the lean and the
   peel-off focus.
   - Remove: the rails and seams, the far light, the floor year numerals, the dust motes, the
     empty ring in the focus state.
   - The current year may appear only as edge typography.

## Curator test (judge every capture)

1. Is anything on screen that isn't a poster or edge typography? If so, fail it and name the
   thing.
2. Can you tell how the posters are organized just by looking, with no labels inside the
   formation?
3. Would this frame be pinned on Pinterest or godly.design? Is it calm, with one mass and
   paper around it?
4. Are the posters big and sharp enough to recognise?
5. Is the motion springy and continuous, with no snaps or blanks?
