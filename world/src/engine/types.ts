/* The contract between the engine and the modes. Changing anything here breaks every
   mode at once: only add, never rename or change meaning (ARCHITECTURE.md). */
import type * as THREE from "three";

export type MediaType = "movie" | "tv" | "book" | "game" | "anime" | "manga" | "music";

export interface TitleNode {
  id: number;                 // === index in WorldData.nodes
  key: string;                // "tmdb:movie:693134"
  title: string;
  year: number | null;
  type: MediaType;
  maker: string;              // director / author / artist / studio
  mine: boolean;              // Armaan logged it (the 990 come first: ids 0..mineCount-1)
  liked: boolean;             // heart, or 8+/10
  rating: number | null;      // 0..10
  logs: number;
  dates: string[];            // each log's date "YYYY-MM-DD", sorted
  first: string | null;
  last: string | null;
  feel: string;               // mood words
  blurb: string;              // ≤ 300 chars
  next: number[];             // 5 nearest in all of Spine (ids; may be unlogged)
  near: number[];             // ≤ 8 nearest among his titles (ids)
  xy: [number, number];       // 2D taste map, 0..1
  sphere: [number, number, number] | null; // unit vector, 3D taste position
  cluster: number | null;     // → WorldData.clusters
  color: string | null;       // dominant poster color "#rrggbb"
  anchor: number | null;      // unlogged titles: his nearest title
  poster: [number, number, number] | null;   // base atlas cell [sheet, col, row]
  posterHi?: [number, number, number] | null; // hi atlas cell (his titles)
}

export interface Cluster { id: number; name: string; color: string; members: number[] }

export interface Atlas { sheets: string[]; cellW: number; cellH: number; cols: number }

export interface WorldData {
  v: number;
  mineCount: number;
  types: Record<MediaType, string>; // "movie" → "Film"
  nodes: TitleNode[];
  clusters: Cluster[];
  atlas: Atlas;    // base: every title, 96×144 cells (albums: 96×96 at the top of the cell)
  atlasHi: Atlas;  // his titles only, 240×360 cells
}

/** Target poses for his posters. Slot i is World.mine[i] (= node id i). Units are world
 *  units; `scale` is the poster's HEIGHT (width follows the artwork's aspect: albums 1:1,
 *  everything else 2:3). Rotation is a quaternion; leave it identity (0,0,0,1) and set
 *  `billboard[i] = 1` to keep a poster facing the camera. Opacity 0 hides a poster. */
export interface PoseBuffer {
  count: number;
  position: Float32Array;   // 3 per poster
  quaternion: Float32Array; // 4 per poster
  scale: Float32Array;      // 1 per poster
  opacity: Float32Array;    // 1 per poster
  billboard: Float32Array;  // 1 per poster: 1 = face the camera
}

export interface MorphOptions {
  duration?: number;   // seconds per poster (default 1.4)
  stagger?: number;    // seconds spread across posters (default 0.6)
  order?: ArrayLike<number>; // a 0..1 delay factor per poster (default: by distance travelled)
  ease?: "outExpo" | "inOutExpo" | "outQuart" | "inOutCubic" | "outCubic";
}

export interface PosterField {
  readonly count: number;            // his titles
  readonly mesh: THREE.InstancedMesh;
  /** A fresh buffer (identity rotation, scale 1, opacity 1, no billboard). */
  makePose(): PoseBuffer;
  /** The pose every poster is easing towards (read only; copy it to edit). */
  readonly target: PoseBuffer;
  /** Morph every poster to `pose` (staggered, eased). Used for mode switches and focus. */
  morphTo(pose: PoseBuffer, opts?: MorphOptions): Promise<void>;
  /** Overwrite targets instantly for posters already settled (per-frame layouts like a
   *  spinning globe). Posters still mid-morph keep easing toward the new target. */
  follow(pose: PoseBuffer): void;
  /** Where a poster is drawn right now (world space). */
  current(i: number, out: THREE.Vector3): THREE.Vector3;
  /** Unlogged titles shown as "ghost" posters (≤ 48 at once). Returns a handle. */
  ghost(id: number, at: THREE.Vector3, opts?: { scale?: number; opacity?: number; quaternion?: THREE.Quaternion; billboard?: boolean }): Ghost;
  clearGhosts(): void;
  /** 0..1 global dim applied to every poster except ids in `except`. */
  dim(amount: number, except?: number[]): void;
  /* ---- additions (engine v1) ---- */
  /** Node id of the poster under the pointer right now (null = none). Modes may react to it
   *  (the shader already lifts/tilts/lights it: nothing to do for the basic effect). */
  readonly hovered: number | null;
  /** Ask for crisp hi-atlas textures for these ids (≤10 are resident at once; the engine also
   *  picks the biggest on-screen posters and the hovered one by itself). */
  sharp(ids: number[]): void;
  /** Key light direction (world space, toward the light) used for the poster shading.
   *  Default (-0.45, 0.7, 0.6). Mutate in place. */
  readonly light: THREE.Vector3;
  /** 0..2 multiplier of the thin edge light (default 1; 0 = off). */
  rimStrength: number;
  /** Shader uniforms of the poster material: uThick (box depth / height, default 0.03),
   *  uSwirl (morph swirl handedness, ±1), uOpacityMul (0..1 global fade). Mutate `.value`. */
  readonly uniforms: Record<string, { value: any }>;
  /** Engine clock (seconds since start) the morph runs on. */
  clock(): number;
}

export interface Ghost { id: number; mesh: THREE.Object3D; set(at: THREE.Vector3, scale?: number, opacity?: number): void; remove(): void }

export interface CameraRig {
  camera: THREE.PerspectiveCamera;
  /** Fly to a position looking at a point (eased; returns when it lands). */
  flyTo(position: THREE.Vector3, lookAt: THREE.Vector3, opts?: { duration?: number; ease?: string; fov?: number; arc?: number }): Promise<void>;
  /** Hand the camera to the mode: it writes position/lookAt every frame (smoothed). */
  readonly position: THREE.Vector3; // desired position (mode may write)
  readonly lookAt: THREE.Vector3;   // desired target (mode may write)
  damping: number;                  // 0..1 per-frame smoothing toward desired (default 0.12)
  /* ---- additions (engine v1) ---- */
  /** The camera is a critically damped spring chasing `position`/`lookAt` (no instant jerk).
   *  0..1 amount of pointer parallax layered on top (default 0.3; 0 = none). Scaled by the
   *  distance to the look-at point and faded to 0 for a second after a flyTo. */
  parallax: number;
  /** 0..1 tiny idle sway (default 1; 0 = off). */
  sway: number;
  /** True while a flyTo is running. */
  readonly flying: boolean;
  /** Extra flyTo option `arc` (0..0.5, default 0.12): bows the path sideways by a fraction of the
   *  chord. Ease names are gsap's ("power3.inOut" default, "expo.out", "power4.inOut"…). */
  /** Stop a running flyTo (the camera keeps its current desired pose). */
  stop(): void;
}

/** Post-processing (pmndrs). All values ease; seconds default 0.8. */
export interface PostSettings {
  grain: number;      // film grain 0..0.2 (default 0.055 dark / 0.07 paper)
  vignette: number;   // darkness 0..1 (default 0.5)
  bloom: number;      // intensity 0..2 (default 0.6); only pixels brighter than bloomThreshold bloom
  dof: number;        // bokeh scale 0..6 (0 = off). Auto-driven by focus; set for a manual look
  aberration: number; // chromatic aberration 0..0.004 (default 0.0006)
}
export interface PostFX {
  readonly settings: PostSettings;
  /** Ease the post settings (partial). */
  set(s: Partial<PostSettings>, seconds?: number): void;
  /** Depth of field focuses on this world point (null = relax to the overview look).
   *  The engine calls it on open()/close() with the focused poster; modes may override. */
  focusOn(world: THREE.Vector3 | null, opts?: { range?: number; bokeh?: number; seconds?: number }): void;
  /** Luminance above which bloom applies (default 1.0). Poster shading never exceeds ~0.85, so
   *  only emissive things you draw with values > 1 (HDR colours) glow. */
  bloomThreshold: number;
}

export interface OrbitOptions {
  target?: THREE.Vector3;
  distance?: number; azimuth?: number; polar?: number; // radians; polar 0 = straight down on +y
  minDistance?: number; maxDistance?: number; minPolar?: number; maxPolar?: number;
  rotateSpeed?: number;  // default 1
  zoomSpeed?: number;    // default 1
  inertia?: number;      // 0..0.99 how long a flick keeps spinning (default 0.92)
  autoRotate?: number;   // radians per second (default 0)
}
/** Damped orbit/drag/zoom. Writes rig.position/lookAt every frame while `enabled`. The engine
 *  routes unused pointer drags + wheel to it (a drag never opens/closes a poster). */
export interface OrbitControls {
  enabled: boolean;
  /** Live orbit centre. Write it to re-centre smoothly. */
  readonly target: THREE.Vector3;
  /** Desired spherical coordinates (they ease; write to steer). */
  azimuth: number; polar: number; distance: number;
  autoRotate: number;
  minDistance: number; maxDistance: number;
  readonly dragging: boolean;
  /** Re-read the spherical state from rig.position/rig.lookAt (call after a flyTo, then enable). */
  syncFromRig(): void;
  /** Add angular velocity (radians/s) that decays with inertia (e.g. to flick a globe). */
  nudge(dAzimuth: number, dPolar: number): void;
  dispose(): void;
}
export interface Controls {
  /** Create the orbit controller for this mode (replaces any previous one). Disposed on exit. */
  orbit(opts?: OrbitOptions): OrbitControls;
}

export interface ModeContext {
  world: WorldData;
  mine: TitleNode[];               // slot i ↔ mine[i]
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  group: THREE.Group;              // the mode's own objects; cleared on exit
  rig: CameraRig;
  posters: PosterField;
  overlay: HTMLElement;            // a div over the canvas for mode-specific DOM (cleared on exit)
  size: { width: number; height: number; dpr: number };
  focused: number | null;
  open(id: number): void;          // focus a title (engine shows the panel, then calls mode.focus)
  close(): void;
  hover(id: number | null, clientX?: number, clientY?: number): void;
  pickPoster(clientX: number, clientY: number): number | null; // node id under the pointer (his posters + ghosts)
  reducedMotion: boolean;
  /* ---- additions (engine v1) ---- */
  post: PostFX;
  controls: Controls;
  /** Clear colour is crossfaded by the engine; read-only current ground colour as a THREE.Color. */
  background: THREE.Color;
  layout: Layout;
  /* ---- additions (v3) ---- */
  /** Burst-style editorial captions pinned to posters on screen (DESIGN.md v3, "Opening a title"). */
  captions: Captions;
}

/** DOM captions in Burst's type (Host Grotesk title, Fragment Mono lines), positioned every frame
 *  from the poster's current on-screen rect. Engine-owned; modes only call these. */
export interface Captions {
  /** Show (or update) the caption for node `id`. side: where it sits relative to the poster
   *  (default "below"). index: e.g. "01.03". emphasis: the focused title (larger title). */
  show(id: number, opts?: { side?: "below" | "above" | "right" | "left"; index?: string; emphasis?: boolean }): void;
  hide(id: number): void;
  clear(): void;
}

/** Screen layout helpers for composing a focused title around the panel (desktop: panel on the
 *  right, free rect = left ~62%; narrow screens (<=760px): panel at the bottom, free rect = top ~48%). */
export interface Layout {
  /** CSS-pixel rect of the screen free of the panel. */
  focusRect(): { x: number; y: number; width: number; height: number; centerX: number; centerY: number };
  /** Centre of that rect in NDC (-1..1). Aim the hero poster here. */
  focusNdc(): { x: number; y: number };
  /** World point at NDC (x, y) `distance` units in front of the camera's current (smoothed) pose. */
  worldAt(ndcX: number, ndcY: number, distance: number, out?: THREE.Vector3): THREE.Vector3;
  /** Camera position + lookAt that frame `target` (poster centre) at `distance` with its centre
   *  on focusNdc(), looking along `dir` (unit vector from the target toward the camera, default +z).
   *  Pass the result to rig.flyTo. */
  frame(target: THREE.Vector3, distance: number, dir?: THREE.Vector3): { position: THREE.Vector3; lookAt: THREE.Vector3 };
}

export interface Mode {
  id: string;            // "orbit"
  name: string;          // "Orbit"
  background: string;    // clear color "#050505"
  theme: "dark" | "light";
  /** Build the world: add objects to ctx.group, morph posters into place, set the camera. */
  enter(ctx: ModeContext): void | Promise<void>;
  /** Every frame. dt seconds, t seconds since enter. */
  update(ctx: ModeContext, dt: number, t: number): void;
  /** A title was opened (also while another is open: travel to it). */
  focus(ctx: ModeContext, id: number): void;
  unfocus(ctx: ModeContext): void;
  /** Pointer + wheel. Return true when the mode used the event (else the engine treats a
   *  click without drag as pick/open/close). */
  pointer?(ctx: ModeContext, e: PointerEvent, phase: "down" | "move" | "up"): boolean;
  wheel?(ctx: ModeContext, e: WheelEvent): boolean;
  key?(ctx: ModeContext, e: KeyboardEvent): boolean;
  resize?(ctx: ModeContext): void;
  /** Leaving: dispose geometry/materials/textures you created. ctx.group is emptied after. */
  exit(ctx: ModeContext): void;
}

/* Set the ground colour via the Mode.background field only: the engine crossfades scene.background. */
