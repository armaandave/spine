/* Burst: a faithful port of Armaan's "Orbit" site (~/projects/playground/mysite/orbit/index.html (his personal page, on his laptop; not in the repo)) onto his real
   Spine data. One 2D canvas + DOM chrome inside ctx.overlay; the three.js poster field stays hidden.
   Every spring/damp constant and the 1/120 substep are the reference's, unchanged.
   Rays = the 16 taste clusters, tiles = his real posters, unfolded frames = hero JPGs. */
import type { Mode, ModeContext, TitleNode } from "../engine/types";
import { PROFILE, NOTES } from "./burst/consts";

let bootUsed = false;
const BOOT_HASH = location.hash; // the engine rewrites the hash before enter(); keep a #ray-N deep link

const TAU = Math.PI * 2;
const BG = "#E7E5E0", DISC = "#EEECE8", INK = "#141413";
const F_SANS = '"Host Grotesk","Schibsted Grotesk","Helvetica Neue",Arial,sans-serif';
const F_MONO = '"Fragment Mono","IBM Plex Mono",Menlo,monospace';
const R0 = 186; // core radius in design px (1440x900)

/* ------------------------------------------------------------------ utils */
function mulberry32(a: number) { return function () { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const rgb = (h: string) => { const n = parseInt(h.slice(1), 16); return [n >> 16 & 255, n >> 8 & 255, n & 255]; };
const toHex = (c: number[]) => "#" + c.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
const mixC = (a: string, b: string, t: number) => { const A = rgb(a), B = rgb(b); return toHex(A.map((v, i) => v + (B[i] - v) * t)); };
const rgba = (h: string, a: number) => { const c = rgb(h); return `rgba(${c[0]},${c[1]},${c[2]},${a})`; };
const clamp = (x: number, a: number, b: number) => x < a ? a : x > b ? b : x;
const sstep = (a: number, b: number, x: number) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const damp = (x: number, t: number, rate: number, h: number) => x + (t - x) * (1 - Math.exp(-rate * h));
function spring(o: any, k: string, vk: string, t: number, w: number, z: number, h: number) { const a = w * w * (t - o[k]) - 2 * z * w * o[vk]; o[vk] += a * h; o[k] += o[vk] * h; }
const fmt = (n: number) => n.toLocaleString("en-US");
const pad2 = (n: number) => String(n).padStart(2, "0");
const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/* ------------------------------------------------------------------ types */
interface Col { G: Group; i: number; a: number; d: number; len: number; ph: number }
interface Tile { G: Group; col: Col; r0: number; lat: number; sc: number; pid: number; kind: number; fl: number; x: number; y: number; w: number; h: number; bc: number; br: number }
interface Item { ix: number; id: number; t: string; m: string; q: number; qv: number; tgt: number; del: number; tile: Tile | null; rect: Rect | null; img: HTMLCanvasElement | null; hv: number }
interface Rect { x: number; y: number; w: number; h: number; up: boolean; anchor: number; left?: boolean; capX?: number; capW?: number; cw?: number }
interface Group {
  gi: number; id: number; name: string; note: string; spoke: string; logs: number; members: number[]; byKind: number[][]; since: number | null;
  a0: number; aw: number; ac: number; L: number; cols: Col[]; tiles: Tile[]; items: Item[]; main: Col; mainLen: number; baseLen: number; order: Tile[];
  alpha: number; hl: number; lenF: number; lenV: number; bandT: number; bandV: number; grow: number; growV: number; delay: number;
  band: { rows: number; cols: number; pitch: number; rowH: number; start: number } | null; palette: string[];
}

/* ------------------------------------------------------------------ poster atlas (his real artwork) */
const CW = 48, CH = 72;          // compact cell: base cell (96x144) halved once
const PDIM = [44, 66], ADIM = [54, 54]; // tile footprints at scale 1: 2:3 posters / 1:1 albums, same area as the reference's mix
let atlasCv: HTMLCanvasElement | null = null;
let AX: Int16Array, AY: Int16Array, AW: Uint8Array, AH: Uint8Array;
let atlasP: Promise<void> | null = null;
const loadImg = (src: string) => new Promise<HTMLImageElement>((res, rej) => { const im = new Image(); im.onload = () => res(im); im.onerror = rej; im.src = src; });

function buildAtlas(ctx: ModeContext): Promise<void> {
  if (atlasP) return atlasP;
  atlasP = (async () => {
    const w = ctx.world, n = w.mineCount, cols = 32, rows = Math.ceil(n / cols);
    const cv = document.createElement("canvas"); cv.width = cols * CW; cv.height = rows * CH;
    const g = cv.getContext("2d")!; g.imageSmoothingQuality = "high";
    AX = new Int16Array(n); AY = new Int16Array(n); AW = new Uint8Array(n); AH = new Uint8Array(n);
    const need = new Set<number>(); for (let i = 0; i < n; i++) need.add(w.nodes[i].poster![0]);
    const sheets = new Map<number, HTMLImageElement>();
    await Promise.all([...need].map(async (s) => { sheets.set(s, await loadImg(w.atlas.sheets[s])); }));
    const { cellW, cellH } = w.atlas;
    for (let i = 0; i < n; i++) {
      const nd = w.nodes[i], p = nd.poster!, x = (i % cols) * CW, y = Math.floor(i / cols) * CH;
      const alb = nd.type === "music", sh = alb ? cellW : cellH;
      g.drawImage(sheets.get(p[0])!, p[1] * cellW, p[2] * cellH, cellW, sh, x, y, CW, alb ? CW : CH);
      AX[i] = x; AY[i] = y; AW[i] = CW; AH[i] = alb ? CW : CH;
    }
    atlasCv = cv;
  })();
  return atlasP;
}

const heroCache = new Map<number, HTMLImageElement | null>();
function loadHero(id: number, cb: () => void) {
  if (heroCache.has(id)) { if (heroCache.get(id)) cb(); return; }
  heroCache.set(id, null);
  loadImg(`data/hero/${id}.jpg`).then((im) => { heroCache.set(id, im); cb(); }).catch(() => {});
}

/* ------------------------------------------------------------------ CSS */
const CSS = `
body.bx-on .panel,body.bx-on .corner-tr{display:none!important}
.bx{--bg:${BG};--disc:${DISC};--ink:${INK};--ink2:#4A4843;--ink3:#86827A;--rule:rgba(20,20,19,.22);--sans:${F_SANS};--mono:${F_MONO};--ease:cubic-bezier(.2,.75,.15,1);
  position:fixed;inset:0;background:var(--bg);color:var(--ink);font-family:var(--sans);-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility;pointer-events:none;overflow:hidden;opacity:0;transition:opacity .9s var(--ease)}
.bx.on{opacity:1}
.bx *{box-sizing:border-box}
.bx canvas{position:absolute;inset:0;display:block}
.bx-tl{position:fixed;left:28px;top:24px;font:9px/1.6 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);z-index:4}
.bx-tr{position:fixed;right:28px;top:22px;text-align:right;z-index:4;transition:opacity .5s var(--ease)}
.bx-tr .f{font:500 13px/1.25 var(--sans);letter-spacing:-.005em}
.bx-tr .m{font:9px/1.7 var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--ink3);margin-top:5px}
.bx-legend{position:fixed;left:28px;bottom:66px;width:226px;z-index:4;transition:opacity .5s var(--ease),transform .6s var(--ease)}
.bx-legend .h{font:9px/1 var(--mono);letter-spacing:.14em;text-transform:uppercase;color:var(--ink3);margin-bottom:9px;display:flex;justify-content:space-between}
.bx-legend .it{display:flex;align-items:baseline;gap:7px;padding:2.5px 0;cursor:pointer;transition:opacity .3s;pointer-events:auto}
.bx-legend .n{font:9px/1 var(--mono);color:var(--ink3);width:15px}
.bx-legend .sw{width:9px;height:6px;flex:none;align-self:center;outline:.5px solid rgba(0,0,0,.25)}
.bx-legend .t{font:400 11.5px/1.15 var(--sans);white-space:nowrap}
.bx-legend .d{flex:1;border-bottom:1px dotted rgba(20,20,19,.28);transform:translateY(-3px);min-width:8px}
.bx-legend .hr{font:9px/1 var(--mono);color:var(--ink3)}
.bx-legend.dim .it{opacity:.32}.bx-legend.dim .it.on{opacity:1}
.bx-readout{position:fixed;right:28px;bottom:58px;text-align:right;font:9px/1.75 var(--mono);letter-spacing:.09em;text-transform:uppercase;color:var(--ink3);z-index:4;transition:opacity .5s var(--ease),transform .6s var(--ease)}
.bx-readout b{font-weight:400;color:var(--ink)}
.bx.ray .bx-legend,.bx.ray .bx-readout{opacity:0;transform:translateY(8px);pointer-events:none}
.bx.ray .bx-legend .it{pointer-events:none}
.bx-core{position:fixed;left:0;top:0;width:372px;height:372px;transform-origin:0 0;pointer-events:none;z-index:3;transition:opacity .5s var(--ease)}
.bx-core .ring{position:absolute;left:0;top:0;width:372px;height:372px;overflow:visible;transform-origin:186px 186px;will-change:transform}
.bx-core .ring text{font-family:var(--mono);font-size:6.6px;letter-spacing:.2em;fill:#7C786F}
.bx-core .kick{position:absolute;top:56px;left:0;right:0;text-align:center;font:8px/1 var(--mono);letter-spacing:.18em;text-transform:uppercase;color:var(--ink3)}
.bx-core .name{position:absolute;top:69px;left:0;right:0;text-align:center;font:500 39px/1 var(--sans);letter-spacing:-.04em;margin:0}
.bx-core .tag{position:absolute;top:115px;left:14px;right:14px;text-align:center;font:400 10.6px/1.3 var(--sans);color:var(--ink2);letter-spacing:-.003em;margin:0}
.bx-core .col{position:absolute;top:145px;width:150px}
.bx-core .col.l{left:18px;text-align:right}
.bx-core .col.r{left:204px}
.bx-core .lab{font:7.5px/1 var(--mono);letter-spacing:.18em;text-transform:uppercase;color:var(--ink3);margin-bottom:8px}
.bx-core .ex{margin-bottom:5px}
.bx-core .ex .nm{display:flex;justify-content:flex-end;align-items:center;gap:4px;font:500 11.6px/1.15 var(--sans);letter-spacing:-.012em}
.bx-core .ex .rl{display:block;font:8px/1.35 var(--mono);color:var(--ink3);letter-spacing:.01em}
.bx-core .fp{margin-bottom:5px}
.bx-core .fp .nm{display:block;font:600 12.4px/1.15 var(--sans);letter-spacing:-.015em;white-space:nowrap}
.bx-core .fp .yr{font:400 8px/1 var(--mono);color:var(--ink3);letter-spacing:.02em;margin-left:3px}
.bx-core .fp .ds{display:block;font:400 9.4px/1.3 var(--sans);color:var(--ink2);letter-spacing:0;white-space:nowrap}
.bx-core .also{font:400 8px/1.6 var(--mono);color:var(--ink3);letter-spacing:.01em;margin-top:1px;padding-top:5px;border-top:1px solid var(--rule);width:128px;white-space:nowrap}
.bx-core .hnt{position:absolute;top:297px;left:0;right:0;text-align:center;font:7.5px/1 var(--mono);letter-spacing:.18em;text-transform:uppercase;color:var(--ink3)}
.bx-rayhead{position:fixed;left:0;top:0;z-index:4;opacity:0;transform:translateY(10px);transition:opacity .45s var(--ease),transform .9s var(--ease);pointer-events:none;width:640px;max-width:calc(100vw - 32px)}
.bx.ray .bx-rayhead{opacity:1;transform:none;transition-delay:.28s}
.bx-rayhead .k{font:9px/1 var(--mono);letter-spacing:.16em;text-transform:uppercase;color:var(--ink3);display:flex;align-items:center;gap:12px}
.bx-rayhead .k i{display:block;width:56px;height:1px;background:var(--ink)}
.bx-rayhead h2{font:500 68px/.92 var(--sans);letter-spacing:-.05em;margin:16px 0 12px;white-space:nowrap}
.bx-rayhead .n{font:400 15px/1.35 var(--sans);color:var(--ink2);margin:0;max-width:520px}
.bx-rayhead .m{font:9px/1.7 var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--ink3);margin-top:12px}
.bx-raynav{position:fixed;right:28px;bottom:58px;z-index:5;display:flex;gap:18px;opacity:0;pointer-events:none;transition:opacity .4s var(--ease)}
.bx.ray .bx-raynav{opacity:1;pointer-events:auto;transition-delay:.5s}
.bx-raynav button{all:unset;cursor:pointer;font:9.5px/1 var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--ink);padding:4px 0;border-bottom:1px solid transparent}
.bx-raynav button:hover{border-color:var(--ink)}
.bx-raynav .x{color:var(--ink3)}
@media (max-width:700px){
  .bx-tl,.bx-legend{display:none}
  .bx-tr{right:16px;top:14px}
  .bx-tr .m span.x{display:none}
  .bx-readout{right:16px;bottom:14px;font-size:8px}
  .bx-rayhead h2{font-size:34px;margin:10px 0 8px}
  .bx-rayhead .n{font-size:12.5px}
  .bx-rayhead .m{display:none}
  .bx-raynav{right:auto;left:16px;bottom:auto;top:16px;gap:12px}
  .bx-raynav .pn{display:none}
  .bx.ray .bx-tr{opacity:0}
  .bx.ray .bx-core{opacity:0}
}`;

/* ------------------------------------------------------------------ state */
let cv: HTMLCanvasElement, c: CanvasRenderingContext2D, root: HTMLElement;
let ctxM: ModeContext;
let groups: Group[] = [], tiles: Tile[] = [];
let W = 0, H = 0, dpr = 1, S = 1, phone = false;
let RM = false;
const st = { mode: "orbit" as "orbit" | "ray", sel: -1, hover: -1, lhover: -1, ihover: -1, rot: -.32, rv: 0, rotT: 0, cx: 0, cy: 0, cxv: 0, cyv: 0, Z: 1, Zv: 0, mx: -9999, my: -9999, lx: -9999, ly: -9999, T: 0 };
let SPIN = TAU / (9 * 60);
let clockT = 0, rdT = 0, coreTf = "", ringTf = "", ready = false;
let els: { core: HTMLElement; ring: HTMLElement; readout: HTMLElement; legend: HTMLElement; rayhead: HTMLElement; rhk: HTMLElement; rhk2: HTMLElement; rht: HTMLElement; rhn: HTMLElement; rhm: HTMLElement; prev: HTMLElement; next: HTMLElement; close: HTMLElement };
let cleanups: (() => void)[] = [];
let downAt: { x: number; y: number } | null = null;
let centerCell = -1;
let focusId = -1;
let gen = 0;
let lnMin = 0, lnMax = 1;
const nrm = (h: number) => clamp((Math.log(h) - lnMin) / (lnMax - lnMin), 0, 1);
let totalLogs = 0;

/* ------------------------------------------------------------------ build burst */
function build(world: ModeContextWorld) {
  const N = world.nodes;
  const cl = world.clusters;
  const logsOf = cl.map((k) => k.members.reduce((a, id) => a + N[id].logs, 0));
  totalLogs = logsOf.reduce((a, b) => a + b, 0);
  lnMin = Math.log(25); lnMax = Math.log(115);
  const ws = logsOf.map((h) => .74 + .56 * nrm(h)); const sum = ws.reduce((a, b) => a + b, 0);
  let a = -Math.PI / 2 - (ws[0] / sum * TAU) / 2;
  groups = cl.map((K, gi) => {
    const aw = ws[gi] / sum * TAU;
    const mem = K.members.slice().sort((p, q) => (N[p].first ?? "9999").localeCompare(N[q].first ?? "9999") || p - q);
    const byKind: number[][] = [[], []]; mem.forEach((id) => byKind[N[id].type === "music" ? 1 : 0].push(id));
    const firsts = mem.map((id) => N[id].first).filter(Boolean) as string[];
    const cols5 = K.members.slice().sort((p, q) => N[q].logs - N[p].logs).slice(0, 5).map((id) => N[id].color || K.color)
      .sort((p, q) => lumOf(p) - lumOf(q));
    const G: Group = {
      gi, id: K.id, name: cap(K.name), note: NOTES[gi] || "", spoke: K.color, logs: logsOf[gi], members: mem, byKind, since: firsts.length ? +firsts[0].slice(0, 4) : null,
      a0: a, aw, ac: a + aw / 2, L: 50 + 192 * Math.pow(nrm(logsOf[gi]), .85), cols: [], tiles: [], items: [], main: null as any, mainLen: 0, baseLen: 0, order: [],
      alpha: 1, hl: 0, lenF: 1, lenV: 0, bandT: 0, bandV: 0, grow: 0, growV: 0, delay: .08 + gi * .035, band: null, palette: cols5,
    };
    a += aw; return G;
  });
  tiles = [];
  groups.forEach((G) => {
    const r = mulberry32(G.gi * 977 + 31); const n = Math.max(7, Math.round(G.aw * R0 / 5.4));
    const main = Math.round(n * (.26 + r() * .48));
    const peaks = [{ c: main, h: 1, s: .7 + r() * .45 }]; const ns = 2 + (r() * 3 | 0);
    for (let i = 0; i < ns; i++) peaks.push({ c: r() * n, h: .2 + r() * .55, s: .55 + r() * 2 });
    const cph = r() * TAU;
    for (let i = 0; i < n; i++) {
      let v = 0; for (const p of peaks) v = Math.max(v, p.h * Math.exp(-((i - p.c) ** 2) / (2 * p.s * p.s)));
      if (i !== main) v *= (.72 + r() * .42);
      const crust = 19 + 9 * (Math.sin(i * .55 + cph) + 1) + r() * 9;
      let len = Math.max(crust, G.L * v);
      if (i !== main && r() < .13) len += G.L * (.12 + r() * .34);
      len = Math.min(len, G.L * .97);
      if (i === main) len = G.L;
      const col: Col = { G, i, a: G.a0 + (i + .5) / n * G.aw, d: ((i + .5) / n - .5) * G.aw, len, ph: r() * TAU };
      G.cols.push(col); if (i === main) G.main = col;
      const step = 7.4;
      for (let r0 = 2.5; r0 < len; r0 += step * (.9 + r() * .2)) {
        const t = r0 / len; if (t > .55 && len > 70 && r() < .09) continue;
        const sc = (.86 + r() * .32) * (1 - .32 * t * t) * .235;
        const tile: Tile = { G, col, r0, lat: (r() - .5) * (3 + 4.5 * t), sc, pid: 0, kind: 0, fl: 0, x: 0, y: 0, w: 0, h: 0, bc: 0, br: 0 };
        G.tiles.push(tile); tiles.push(tile);
      }
    }
    G.mainLen = G.main.len;
    G.baseLen = G.cols.map((q) => q.len).sort((p, q) => p - q)[Math.floor(n * .4)];
    G.order = [...G.tiles].sort((p, q) => (p.r0 + p.col.i * .013) - (q.r0 + q.col.i * .013)); // rim → tip
    // posters along the strip by first-log date (oldest at the rim); least-used first so repeats stay apart
    const M = G.members.length, use = new Uint16Array(M), win = Math.max(2, Math.round(M * .12)), NT = G.order.length;
    G.order.forEach((t, i) => {
      const cc = Math.round(NT > 1 ? i / (NT - 1) * (M - 1) : 0), lo = Math.max(0, cc - win), hi = Math.min(M - 1, cc + win);
      let best = lo, bs = 1e9; for (let j = lo; j <= hi; j++) { const s = use[j] + r() * .5; if (s < bs) { bs = s; best = j; } }
      use[best]++; t.pid = G.members[best]; t.kind = N[t.pid].type === "music" ? 1 : 0;
    });
    // unfolded items: liked first, then rating, then logs; shown in date order
    const nI = M >= 50 ? 12 : 10;
    const pick = G.members.slice().sort((p, q) => (+N[q].liked - +N[p].liked) || ((N[q].rating ?? -1) - (N[p].rating ?? -1)) || (N[q].logs - N[p].logs) || p - q).slice(0, nI)
      .sort((p, q) => (N[p].first ?? "9999").localeCompare(N[q].first ?? "9999") || p - q);
    G.items = pick.map((id, i) => ({ ix: i, id, t: N[id].title, m: itemNote(N[id]), q: 0, qv: 0, tgt: 0, del: 0, tile: null, rect: null, img: null, hv: 0 }));
  });
  tiles.sort((p, q) => q.r0 - p.r0);
}
type ModeContextWorld = ModeContext["world"];
const lumOf = (h: string) => { const q = rgb(h); return (.2126 * q[0] + .7152 * q[1] + .0722 * q[2]) / 255; };
function itemNote(n: TitleNode): string {
  const a: string[] = [], b: string[] = [];
  if (n.year) a.push(String(n.year));
  if (n.maker) a.push(n.maker);
  if (n.rating != null) { const s = Math.round(n.rating) / 2; b.push("★ " + (Number.isInteger(s) ? s + ".0" : String(s))); }
  b.push(`${n.logs} ${n.logs === 1 ? "log" : "logs"}`);
  return a.join(" · ") + "|" + b.join(" · "); // two caption lines
}

/* ------------------------------------------------------------------ layout */
function baseLayout() { return phone ? { cx: W / 2, cy: H * .45, Z: 1 } : { cx: W / 2, cy: H / 2, Z: 1 }; }
function rayLayout() {
  if (!phone) { const Z = 1.1, k = S * Z, cx = W * .2, cy = H * .535, R = R0 * k, x1 = W - 56 * S;
    return { cx, cy, Z, k, R, dir: 0, lenF: 1, x0: cx + R + 52 * S, x1, y0: 0, y1: 0, bandLen: (x1 - cx - R) / k - 16 }; }
  const Z = .5, k = S * Z, R = R0 * k, cx = W - 30, cy = R * .55;
  return { cx, cy, Z, k, R, dir: Math.PI / 2, lenF: 1, x0: 0, x1: 0, y0: Math.max(178, cy + R + 24), y1: H - 92, bandLen: (H - 92 - cy - R) / k - 16 };
}
const targetLayout = (): { cx: number; cy: number; Z: number; lenF: number } => st.mode === "ray" ? rayLayout() : { ...baseLayout(), lenF: 1 };

function resize() {
  W = innerWidth; H = innerHeight; dpr = Math.min(2, window.devicePixelRatio || 1); phone = W < 700;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr); cv.style.width = W + "px"; cv.style.height = H + "px";
  S = phone ? Math.min(W / 465, H / 1010) : Math.min(W / 1440, H / 900);
  const L = targetLayout(); if (!st.cx) { st.cx = L.cx; st.cy = L.cy; st.Z = L.Z; }
  if (st.mode === "ray") { fitHead(groups[st.sel]); placeItems(groups[st.sel]); }
}

/* ------------------------------------------------------------------ items (screen 2) */
function coverRect(iw: number, ih: number, w: number, h: number) { const ia = iw / ih, ta = w / h; let sw = iw, sh = ih; if (ia > ta) sw = sh * ta; else sh = sw / ta; return { sx: (iw - sw) / 2, sy: (ih - sh) / 2, sw, sh }; }
function paintItem(it: Item) {
  const cnv = it.img!, g = cnv.getContext("2d")!; g.imageSmoothingQuality = "high"; g.clearRect(0, 0, cnv.width, cnv.height);
  const him = heroCache.get(it.id);
  if (him) { const cr = coverRect(him.width, him.height, cnv.width, cnv.height); g.drawImage(him, cr.sx, cr.sy, cr.sw, cr.sh, 0, 0, cnv.width, cnv.height); }
  else if (atlasCv) { const cr = coverRect(AW[it.id], AH[it.id], cnv.width, cnv.height); g.drawImage(atlasCv, AX[it.id] + cr.sx, AY[it.id] + cr.sy, cr.sw, cr.sh, 0, 0, cnv.width, cnv.height); }
}
let headBottom = 0;
function placeItems(G: Group) {
  const L = rayLayout(), k = L.k, R = L.R, n = G.items.length;
  const N = G.order.length, rows = Math.max(2, Math.ceil(N * 15 / L.bandLen)), cols = Math.ceil(N / rows), pitch = L.bandLen / (cols + .5), rowH = 12.6;
  G.order.forEach((t, i) => { t.bc = Math.floor(i / rows); t.br = i % rows; });
  G.band = { rows, cols, pitch, rowH, start: 16 };
  const off = (rows * rowH / 2 + 28) * k;
  const nodes = ctxM.world.nodes;
  if (!phone) {
    const gap = 16 * S;
    // portrait frames: height limited by the room under the header (above the band) and by the caption above it
    const capUp = 66 * S, avail = (L.cy - off) - capUp - headBottom;
    const fh = clamp(Math.min(avail, 230 * S), 96 * S, 230 * S), fwN = fh * 2 / 3;
    const sp = (L.x1 - L.x0 - fwN) / Math.max(1, n - 1), capW = Math.max(fwN, 2 * sp - 12 * S);
    G.items.forEach((it, i) => {
      const alb = nodes[it.id].type === "music";
      const h = alb ? Math.min(fh, 2 * sp - gap) : fh, w = alb ? h : fwN;
      const xc = L.x0 + fwN / 2 + sp * i, up = i % 2 === 0;
      it.rect = { x: xc - w / 2, y: up ? L.cy - off - h : L.cy + off, w, h, up, anchor: xc, capW: Math.max(w, Math.min(capW, W - 56 * S - (xc - w / 2))) };
    });
  } else {
    const sh = (L.y1 - L.y0) / n, fh = Math.min(sh - 10, 58), fw = fh * 1.5;
    G.items.forEach((it, i) => { const yc = L.y0 + sh * (i + .5);
      it.rect = { x: 16, y: yc - fh / 2, w: fw, h: fh, up: false, left: true, anchor: yc, capX: 16 + fw + 12, capW: L.cx - off + 14 - (16 + fw + 12) }; });
  }
  // origin tiles: the strip tile on the facing edge nearest each frame
  const used = new Set<Tile>();
  G.items.forEach((it) => {
    const wantRow = !phone ? (it.rect!.up ? 0 : rows - 1) : rows - 1;
    let best: Tile | null = null, bc = 1e9;
    for (const t of G.order) { if (used.has(t)) continue;
      const along = (phone ? L.cy : L.cx) + R + (G.band!.start + (t.bc + .5 + (t.br % 2) * .5) * pitch) * k;
      const cc = Math.abs(along - it.rect!.anchor) + Math.abs(t.br - wantRow) * 400; if (cc < bc) { bc = cc; best = t; } }
    used.add(best!); it.tile = best;
  });
  G.items.forEach((it) => {
    const pw = Math.max(2, Math.round(it.rect!.w * dpr)), ph = Math.max(2, Math.round(it.rect!.h * dpr));
    if (!it.img) it.img = document.createElement("canvas");
    it.img.width = pw; it.img.height = ph; paintItem(it);
    loadHero(it.id, () => { if (it.img && it.rect) paintItem(it); });
  });
}

/* header text + size, measured before the frames are placed */
function fitHead(G: Group) {
  const rh = els.rayhead, L = rayLayout();
  if (!phone) { rh.style.left = L.x0 + "px"; rh.style.top = Math.round(88 * S) + "px"; rh.style.width = Math.min(640, W - L.x0 - 40) + "px"; }
  else { rh.style.left = "16px"; rh.style.top = "50px"; rh.style.width = (W - 110) + "px"; }
  const base = phone ? 34 : 68, avail = (phone ? W - 110 : Math.min(640, W - L.x0 - 40));
  const g = c; g.font = `500 ${base}px ${F_SANS}`;
  const wd = g.measureText(G.name).width - G.name.length * base * .05;
  const fs = wd > avail ? Math.max(30, base * avail / wd) : base;
  els.rht.style.fontSize = fs.toFixed(1) + "px";
  headBottom = rh.offsetTop + rh.offsetHeight + 8 * S;
}

function openRay(gi: number, fromUser?: boolean) {
  if (gi < 0 || !ready) return; const G = groups[gi];
  if (st.mode === "ray" && st.sel === gi) return;
  if (fromUser) focusId = -1;
  if (st.mode === "ray" && st.sel >= 0) groups[st.sel].items.forEach((it) => { it.tgt = 0; it.del = 0; });
  st.mode = "ray"; st.sel = gi; st.hover = -1; st.lhover = -1; legendSync();
  const L = rayLayout(); let t = L.dir - G.ac; t += Math.round((st.rot - t) / TAU) * TAU; st.rotT = t;
  els.rhk.textContent = `R/${pad2(gi + 1)}`;
  els.rhk2.textContent = `Fig. 1.${pad2(gi + 1)} — unfolded`;
  els.rht.textContent = G.name;
  els.rhn.textContent = `${G.note}.`;
  els.rhm.textContent = `${G.members.length} titles · ${fmt(G.logs)} logs` + (G.since ? ` · since ${G.since}` : "");
  fitHead(G); placeItems(G);
  G.items.forEach((it, i) => { it.tgt = 1; it.del = (RM ? .1 : .34) + i * (RM ? .015 : .05); });
  const p = (gi + groups.length - 1) % groups.length, nx = (gi + 1) % groups.length;
  els.prev.textContent = `← ${pad2(p + 1)} ${groups[p].name}`;
  els.next.textContent = `${pad2(nx + 1)} ${groups[nx].name} →`;
  root.classList.add("ray");
  if (fromUser) history.replaceState(null, "", "#ray-" + G.id);
}
function ensureItem(G: Group, id: number) {
  if (G.items.some((it) => it.id === id)) return;
  const N = ctxM.world.nodes, score = (i: number) => (+N[i].liked) * 1e6 + (N[i].rating ?? -1) * 1e3 + N[i].logs;
  let wi = 0; G.items.forEach((it, i) => { if (score(it.id) < score(G.items[wi].id)) wi = i; });
  const fresh: Item = { ix: 0, id, t: N[id].title, m: itemNote(N[id]), q: 0, qv: 0, tgt: 0, del: 0, tile: null, rect: null, img: null, hv: 0 };
  G.items.splice(wi, 1, fresh);
  G.items.sort((p, q) => (N[p.id].first ?? "9999").localeCompare(N[q.id].first ?? "9999") || p.id - q.id);
  G.items.forEach((it, i) => { it.ix = i; });
}
function leave() { if (ctxM.focused != null) ctxM.close(); else closeRay(true); }
function closeRay(fromUser?: boolean) {
  if (st.mode !== "ray") return; const G = groups[st.sel];
  G.items.forEach((it, i, a) => { it.tgt = 0; it.del = (a.length - i) * .018; });
  st.mode = "orbit"; st.sel = -1; st.hover = -1; legendSync(); root.classList.remove("ray");
  if (fromUser) history.replaceState(null, "", "#burst");
}

/* ------------------------------------------------------------------ hit tests */
function polarAt(x: number, y: number) { const dx = x - st.cx, dy = y - st.cy; let a = Math.atan2(dy, dx) - st.rot; const a0 = groups[0].a0; a = ((a - a0) % TAU + TAU) % TAU + a0; return { r: Math.hypot(dx, dy), a }; }
function groupAt(x: number, y: number) {
  const k = S * st.Z, R = R0 * k, p = polarAt(x, y); if (p.r < R * .985) return -1;
  for (const G of groups) if (p.a >= G.a0 && p.a < G.a0 + G.aw) {
    if (st.mode === "ray" && G.gi === st.sel) return -1;
    const reach = R + (G.L * G.lenF * 1.06 + 28) * k; return p.r < reach ? G.gi : -1;
  }
  return -1;
}
function itemAt(x: number, y: number) {
  if (st.mode !== "ray") return -1; const G = groups[st.sel];
  for (const it of G.items) { const r = it.rect!; if (it.q > .8 && x >= r.x - 4 && x <= r.x + r.w + 4 && y >= r.y - 34 && y <= r.y + r.h + 34) return it.ix; }
  return -1;
}

/* ------------------------------------------------------------------ simulation */
function step(h: number) {
  st.T += h;
  st.lx = damp(st.lx, st.mx, 13, h); st.ly = damp(st.ly, st.my, 13, h);
  const L = targetLayout();
  spring(st, "cx", "cxv", L.cx, 6.4, .88, h); spring(st, "cy", "cyv", L.cy, 6.4, .88, h); spring(st, "Z", "Zv", L.Z, 6.4, .88, h);
  if (st.mode === "ray") spring(st, "rot", "rv", st.rotT, 5.6, .8, h);
  else { st.rv = damp(st.rv, SPIN, 1.4, h); st.rot += st.rv * h; }
  const hv = st.lhover >= 0 ? st.lhover : st.hover;
  for (const G of groups) {
    const sel = st.mode === "ray" && G.gi === st.sel;
    let aT, hT, lT, bT;
    if (st.mode === "ray") { aT = sel ? 1 : .13; hT = 0; lT = sel ? L.lenF : .3; bT = sel ? 1 : 0; }
    else { aT = hv < 0 ? 1 : (G.gi === hv ? 1 : .15); hT = G.gi === hv ? 1 : 0; lT = G.gi === hv ? 1.07 : 1; bT = 0; }
    G.alpha = damp(G.alpha, aT, 7.5, h); G.hl = damp(G.hl, hT, 9, h);
    spring(G, "lenF", "lenV", lT, sel ? 5.8 : 8.5, sel ? .82 : .52, h);
    spring(G, "bandT", "bandV", bT, 6, .94, h);
    for (const it of G.items) { if (it.del > 0) { it.del -= h; continue; } spring(it, "q", "qv", it.tgt, 9.2, .66, h); }
  }
}

/* ------------------------------------------------------------------ render */
function wrapClip(g: CanvasRenderingContext2D, text: string, maxW: number) { if (g.measureText(text).width <= maxW) return text; let t = text; while (t.length > 1 && g.measureText(t + "…").width > maxW) t = t.slice(0, -1); return t.trimEnd() + "…"; }
function wrapLines(g: CanvasRenderingContext2D, text: string, maxW: number, max: number) {
  const words = text.split(" "); const out: string[] = []; let cur = "";
  for (const wd of words) { const t = cur ? cur + " " + wd : wd; if (g.measureText(t).width <= maxW || !cur) cur = t; else { out.push(cur); cur = wd; } }
  if (cur) out.push(cur);
  if (out.length > max) { const keep = out.slice(0, max); keep[max - 1] = wrapClip(g, out.slice(max - 1).join(" "), maxW); return keep; }
  return out.map((l) => wrapClip(g, l, maxW));
}

function draw(dt: number) {
  c.setTransform(dpr, 0, 0, dpr, 0, 0); c.globalAlpha = 1; c.globalCompositeOperation = "source-over";
  c.fillStyle = BG; c.fillRect(0, 0, W, H);
  if (!atlasCv) return;
  const cx = st.cx, cy = st.cy, k = S * st.Z, R = R0 * k, rot = st.rot, T = st.T;
  const breath = 1 + (RM ? .006 : .026) * Math.sin(T * TAU / 8.5);

  c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.fillStyle = DISC; c.fill();
  c.lineWidth = .55;
  for (const G of groups) {
    c.beginPath();
    for (const col of G.cols) { const a = rot + col.a; c.moveTo(cx + Math.cos(a) * 2.5 * k, cy + Math.sin(a) * 2.5 * k); c.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R); }
    c.strokeStyle = G.hl > .02 ? mixC(G.spoke, "#141413", .1) : G.spoke;
    c.globalAlpha = (.3 + .55 * G.hl) * (.25 + .75 * G.alpha) * G.grow; c.stroke();
  }
  c.globalAlpha = 1;
  const fg = c.createRadialGradient(cx, cy, 0, cx, cy, R);
  fg.addColorStop(0, rgba(DISC, 0)); fg.addColorStop(.07, rgba(DISC, 0)); fg.addColorStop(.16, rgba(DISC, .72)); fg.addColorStop(.66, rgba(DISC, .76)); fg.addColorStop(.9, rgba(DISC, 0));
  c.fillStyle = fg; c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.fill();
  c.strokeStyle = INK; c.globalAlpha = .42; c.lineWidth = .7; c.beginPath(); c.arc(cx, cy, R, 0, TAU); c.stroke();
  c.globalAlpha = .3; c.lineWidth = .5; c.beginPath();
  for (let i = 0; i < 120; i++) { const a = rot + i / 120 * TAU, l = (i % 10 === 0 ? 7 : 3) * k; c.moveTo(cx + Math.cos(a) * (R - l), cy + Math.sin(a) * (R - l)); c.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R); }
  c.stroke(); c.globalAlpha = 1;

  // center: his most-logged title, small and framed
  { const ph = 27 * k, pw = ph * AW[centerCell] / AH[centerCell];
    c.drawImage(atlasCv, AX[centerCell], AY[centerCell], AW[centerCell], AH[centerCell], cx - pw / 2, cy - ph / 2, pw, ph);
    c.strokeStyle = INK; c.lineWidth = .6; c.globalAlpha = .8; c.strokeRect(cx - pw / 2, cy - ph / 2, pw, ph); c.globalAlpha = 1; }

  // shimmer: a tile flashes and swaps to another poster of its ray
  if (!RM && dt > 0) { let n = dt * 16; while (n > 0) { if (Math.random() < n) { const t = tiles[(Math.random() * tiles.length) | 0]; t.fl = 1; const L = t.G.byKind[t.kind]; t.pid = L[(Math.random() * L.length) | 0]; } n -= 1; } }
  const fdec = Math.exp(-dt * 2.4);

  const RP = 64 * S, RP2 = RP * RP, mxl = st.lx, myl = st.ly, rayMode = st.mode === "ray";
  for (const t of tiles) {
    const G = t.G, col = t.col;
    const vis = clamp((G.grow * (col.len + 14) - t.r0) / 10, 0, 1); if (vis <= 0) { t.w = 0; continue; }
    const lf = G.lenF * (1 + (.045 * (1 - G.bandT)) * Math.sin(T * .75 + col.ph)) * (G.bandT > .5 ? 1 : breath);
    const rr = R + t.r0 * lf * k;
    const aB = rot + col.a, ca = Math.cos(aB), sa = Math.sin(aB);
    let x = cx + ca * rr - sa * t.lat * k, y = cy + sa * rr + ca * t.lat * k;
    if (G.bandT > .0005 && G.band) { const B = G.band, aC = rot + G.ac, cc = Math.cos(aC), sc = Math.sin(aC);
      const al = R + (B.start + (t.bc + .5 + (t.br % 2) * .5) * B.pitch) * k, lat = (t.br - (B.rows - 1) / 2) * B.rowH * k;
      const xb = cx + cc * al - sc * lat, yb = cy + sc * al + cc * lat; x += (xb - x) * G.bandT; y += (yb - y) * G.bandT; }
    const dx = x - mxl, dy = y - myl, d2 = dx * dx + dy * dy;
    if (d2 < RP2 && !rayMode) { const d = Math.sqrt(d2) + .001, f = (1 - d / RP) ** 2 * 7 * k; x += dx / d * f; y += dy / d * f; }
    const z = (t.sc + (.235 * 1.16 - t.sc) * G.bandT) * k * vis * (1 + .06 * G.hl);
    const w = (t.kind ? ADIM[0] : PDIM[0]) * z, h = (t.kind ? ADIM[1] : PDIM[1]) * z;
    t.x = x; t.y = y; t.w = w; t.h = h;
    const a = G.alpha; if (a < .01) continue;
    c.globalAlpha = a;
    c.drawImage(atlasCv, AX[t.pid], AY[t.pid], AW[t.pid], AH[t.pid], x - w / 2, y - h / 2, w, h);
    if (t.fl > .02) { c.globalAlpha = a * t.fl * .42; c.fillStyle = "#FFFFFF"; c.fillRect(x - w / 2, y - h / 2, w, h); t.fl *= fdec; }
  }
  c.globalAlpha = 1;

  // tip numerals
  c.font = `${8 * k}px ${F_MONO}`; c.textAlign = "center"; c.textBaseline = "middle"; c.fillStyle = INK;
  for (const G of groups) {
    const a = .5 * G.grow * (1 - G.hl) * (1 - G.bandT) * (rayMode ? 0 : G.alpha); if (a < .02) continue;
    const m = G.main, ang = rot + m.a, rr = R + (m.len * G.lenF * breath + 11) * k;
    c.globalAlpha = a; c.fillText(pad2(G.gi + 1), cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr);
  }
  c.globalAlpha = 1;

  if (!rayMode) for (const G of groups) if (G.hl > .02) drawLabel(G, k, R);
  for (const G of groups) { if (G.items.length && (G.bandT > .02 || G.items.some((it) => it.q > .003 || it.tgt > 0))) drawItems(G, k, R); }
}

function drawLabel(G: Group, k: number, R: number) {
  const C = G, a = G.hl * G.alpha, m = G.main, ang = st.rot + m.a;
  const flip = Math.cos(ang) < 0, side = (m.d >= 0 ? 1 : -1) * (flip ? -1 : 1), off = 15 * k;
  const t0 = `R/${pad2(G.gi + 1)}`, t1 = `${C.name}`, th = `${fmt(G.members.length)} titles · ${fmt(G.logs)} logs`;
  const f0 = `${8.4 * k}px ${F_MONO}`, f1 = `500 ${15 * k}px ${F_SANS}`, f2 = `${9.4 * k}px ${F_MONO}`;
  c.save(); c.translate(st.cx, st.cy); c.rotate(flip ? ang + Math.PI : ang);
  c.font = f0; const w0 = c.measureText(t0).width + 7 * k; c.font = f1; const w1 = c.measureText(t1).width;
  c.font = f2; const notes = wrapLines(c, `${C.note} · ${th}`, Math.max(230 * k, w0 + w1), 2);
  const wt = Math.max(w0 + w1, ...notes.map((l) => c.measureText(l).width));
  let rS = R + (G.baseLen * 1.15 * G.lenF + 16) * k;
  { const dx = Math.cos(ang), dy = Math.sin(ang), mx = 44, mt = 92, mb = 48;
    const tx = dx > 1e-4 ? (W - mx - st.cx) / dx : dx < -1e-4 ? (mx - st.cx) / dx : 1e9, ty = dy > 1e-4 ? (H - mb - st.cy) / dy : dy < -1e-4 ? (mt - st.cy) / dy : 1e9;
    const rMax = Math.min(tx, ty) - 10 * k; if (rS + wt > rMax) rS = Math.max(R + 8 * k, rMax - wt); }
  const lh1 = 17 * k, lh2 = 12.5 * k, blockH = lh1 + lh2 * notes.length;
  const top = side > 0 ? off : -off - blockH;
  const slide = (1 - G.hl) * 16 * k, xs = flip ? -(rS + slide) : (rS + slide), dir = flip ? -1 : 1, ruleY = side > 0 ? off - 4 * k : -off + 4 * k;
  c.strokeStyle = INK; c.lineWidth = .6; c.globalAlpha = a * .55; c.beginPath(); c.moveTo(dir * R, ruleY); c.lineTo(dir * (rS + slide + wt), ruleY);
  c.moveTo(dir * R, ruleY - 3 * k); c.lineTo(dir * R, ruleY + 3 * k); c.stroke();
  c.globalAlpha = a; c.lineJoin = "round"; c.textBaseline = "alphabetic";
  const halo = (txt: string, x: number, y: number) => { c.lineWidth = 4.2 * k; c.strokeStyle = BG; c.strokeText(txt, x, y); c.fillText(txt, x, y); };
  const y1 = top + 13 * k;
  if (!flip) { c.textAlign = "left"; c.font = f0; c.fillStyle = "#86827A"; halo(t0, xs, y1); c.font = f1; c.fillStyle = INK; halo(t1, xs + w0, y1); }
  else { c.textAlign = "right"; c.font = f1; c.fillStyle = INK; halo(t1, xs, y1); c.font = f0; c.fillStyle = "#86827A"; halo(t0, xs - w1 - 7 * k, y1); }
  c.font = f2; c.fillStyle = "#4A4843"; notes.forEach((l, i) => halo(l, xs, top + lh1 + lh2 * (i + 1) - 2 * k));
  c.restore();
}

function drawItems(G: Group, k: number, R: number) {
  const bt = G.bandT, B = G.band;
  if (bt > .02 && B) {
    const aC = st.rot + G.ac, a0 = R + B.start * k * .5, a1 = R + (B.start + (B.cols + .5) * B.pitch) * k, ly = (B.rows * B.rowH / 2 + 6) * k;
    c.save(); c.translate(st.cx, st.cy); c.rotate(aC);
    c.globalAlpha = bt * .55; c.strokeStyle = INK; c.lineWidth = .6; c.beginPath(); c.moveTo(a0, ly); c.lineTo(a1, ly);
    for (let i = 0; i <= 20; i++) { const x = a0 + (a1 - a0) * i / 20, l = (i % 10 === 0 ? 5 : i % 2 === 0 ? 2.6 : 0) * k; if (l) { c.moveTo(x, ly); c.lineTo(x, ly + l); } }
    c.stroke();
    if (!phone) { c.globalAlpha = bt * .85; c.fillStyle = "#86827A"; c.font = `${8 * k}px ${F_MONO}`; c.textBaseline = "top";
      c.textAlign = "left"; const ax = R + (B.start + 26) * k; c.fillText(`0 → ${fmt(G.logs)} LOGS`, ax, ly + 8 * k); c.fillText("OLD → NEW", ax, ly + 19 * k); }
    c.restore(); c.globalAlpha = 1;
  }
  for (const it of G.items) {
    const q = it.q; if (q < .003 || !it.tile || !it.rect) continue;
    const t = it.tile, ox = t.x - t.w / 2, oy = t.y - t.h / 2, ow = t.w || 1, oh = t.h || 1, r = it.rect;
    const foc = it.id === focusId, hov = (st.ihover === it.ix || foc) ? 1 : 0; it.hv = damp(it.hv || 0, hov, 14, 1 / 60);
    const lift = 1 + .035 * it.hv;
    const w = ow + (r.w * lift - ow) * q, h = oh + (r.h * lift - oh) * q;
    const x = (ox + ow / 2) + ((r.x + r.w / 2) - (ox + ow / 2)) * q - w / 2, y = (oy + oh / 2) + ((r.y + r.h / 2) - (oy + oh / 2)) * q - h / 2;
    if (w < .5 || h < .5) continue;
    const la = sstep(.45, 1, q) * (.45 + .55 * it.hv);
    if (la > .01) { c.globalAlpha = la; c.strokeStyle = INK; c.lineWidth = .6; c.beginPath();
      let ex, ey; if (!phone) { ex = x + w / 2; ey = r.up ? y + h : y; } else { ex = r.capX! + r.capW! + 4; ey = y + h / 2; }
      c.moveTo(t.x, t.y); c.lineTo(ex, ey); c.stroke();
      c.beginPath(); c.arc(t.x, t.y, 1.6, 0, TAU); c.fillStyle = INK; c.fill();
      c.strokeRect(t.x - t.w / 2 - 1.5, t.y - t.h / 2 - 1.5, t.w + 3, t.h + 3);
    }
    c.globalAlpha = 1;
    c.drawImage(it.img!, x, y, w, h);
    c.globalAlpha = sstep(.5, 1, q) * (foc ? .9 : .25); c.strokeStyle = INK; c.lineWidth = foc ? 1 : .5; c.strokeRect(x + .25, y + .25, w - .5, h - .5);
    if (foc && la > .01) { c.globalAlpha = la; c.lineWidth = .7; c.beginPath(); c.arc(t.x, t.y, Math.max(t.w, t.h) * .5 + 5, 0, TAU); c.stroke(); }
    const ca = sstep(.78, 1, q); if (ca < .01) continue;
    c.globalAlpha = ca; c.textBaseline = "alphabetic"; c.textAlign = "left";
    const tS = Math.max(10.5, 12.4 * S), mS = Math.max(8, 8.8 * S), tL = tS * 1.16, mL = mS * 1.45;
    const fT = `500 ${tS}px ${F_SANS}`, fM = `${mS}px ${F_MONO}`;
    const idxs = `${pad2(G.gi + 1)}.${pad2(it.ix + 1)}`;
    if (phone) { const cw = r.capW!; c.font = fT; c.fillStyle = INK; c.fillText(wrapClip(c, it.t, cw), r.capX!, y + tS * .95);
      c.font = fM; c.fillStyle = "#86827A"; wrapLines(c, idxs + "  " + it.m.replace("|", " · "), cw, 2).forEach((l, i) => c.fillText(l, r.capX!, y + tS * .95 + mL * 1.15 + i * mL)); continue; }
    const cw = r.capW!;
    c.font = fT; const tl = wrapLines(c, it.t, cw, 2); c.font = fM; const ml = it.m.split("|").filter(Boolean).map((l) => wrapClip(c, l, cw));
    let yy: number;
    if (r.up) { yy = y - 7 * S - (ml.length - 1) * mL; c.font = fM; c.fillStyle = "#86827A"; ml.forEach((l, i) => c.fillText(l, x, yy + i * mL));
      yy -= mL + (tl.length - 1) * tL + 1 * S; c.font = fT; c.fillStyle = INK; tl.forEach((l, i) => c.fillText(l, x, yy + i * tL)); }
    else { yy = y + h + tS + 5 * S; c.font = fT; c.fillStyle = INK; tl.forEach((l, i) => c.fillText(l, x, yy + i * tL));
      yy += (tl.length - 1) * tL + mL + 1 * S; c.font = fM; c.fillStyle = "#86827A"; ml.forEach((l, i) => c.fillText(l, x, yy + i * mL)); }
    c.font = `${Math.max(7.5, 8 * S)}px ${F_MONO}`; c.fillStyle = INK; c.textAlign = "left";
    c.fillText(idxs, x + w / 2 + 5 * S, r.up ? y + h + 11 * S : y - 5 * S);
  }
  c.globalAlpha = 1;
}

/* ------------------------------------------------------------------ legend */
function legendSync() { const hv = st.lhover >= 0 ? st.lhover : st.hover; els.legend.classList.toggle("dim", hv >= 0); const its = els.legend.children; for (let i = 1; i < its.length; i++) its[i].classList.toggle("on", i - 1 === hv); }
function buildLegend() {
  groups.forEach((G, gi) => {
    const el = document.createElement("div"); el.className = "it";
    el.innerHTML = `<span class="n">${pad2(gi + 1)}</span><span class="sw" style="background:linear-gradient(90deg,${G.palette.join(",")})"></span><span class="t">${G.name}</span><span class="d"></span><span class="hr">${fmt(G.logs)}</span>`;
    el.addEventListener("mouseenter", () => { st.lhover = gi; legendSync(); }); el.addEventListener("mouseleave", () => { st.lhover = -1; legendSync(); });
    el.addEventListener("click", () => openRay(gi, true));
    els.legend.appendChild(el);
  });
}

/* ------------------------------------------------------------------ DOM */
function buildDom(ov: HTMLElement) {
  if (!document.getElementById("bx-style")) {
    const s = document.createElement("style"); s.id = "bx-style"; s.textContent = CSS; document.head.append(s);
    const l = document.createElement("link"); l.rel = "stylesheet"; l.href = "https://fonts.googleapis.com/css2?family=Host+Grotesk:wght@300..700&family=Fragment+Mono&display=swap"; document.head.append(l);
  }
  root = document.createElement("div"); root.className = "bx";
  const P = PROFILE;
  root.innerHTML = `
<canvas aria-label="A radial burst of his posters, one ray per taste cluster"></canvas>
<div class="bx-tl">Spine — Nº 01<br>Plate, 22.11.2026</div>
<div class="bx-tr"><div class="f">Fig. 1 — Everything I’ve logged</div><div class="m" style="margin-top:2px">from the Spine library, ${P.since} – 2026</div>
  <div class="m">16 rays · ${fmt(P.titles)} titles<span class="x"> · length ∝ log logs</span></div></div>
<div class="bx-legend"><div class="h"><span>Rays</span><span>logs</span></div></div>
<div class="bx-readout"></div>
<div class="bx-core">
  <svg class="ring" viewBox="0 0 372 372" aria-hidden="true"><defs><path id="bxringp" d="M186,186 m-174,0 a174,174 0 1,1 348,0 a174,174 0 1,1 -348,0"/></defs>
    <text><textPath href="#bxringp" textLength="1086" lengthAdjust="spacing">${P.ring}</textPath></text></svg>
  <div class="kick">${P.handle}</div>
  <h1 class="name" style="margin:0">${P.name}</h1>
  <p class="tag">${P.tag}</p>
  <div class="col l"><div class="lab">By type</div>${P.byType.map(([a, b]) => `<div class="ex"><span class="nm">${a}</span><span class="rl">${b}</span></div>`).join("")}</div>
  <div class="col r"><div class="lab">Most logged</div>${P.mostLogged.map(([a, y, d]) => `<div class="fp"><span class="nm">${a} <span class="yr">${y}</span></span><span class="ds">${d}</span></div>`).join("")}
    <div class="also">${P.also.join("<br>")}</div></div>
  <div class="hnt">The rays are my taste · hover · click</div>
</div>
<section class="bx-rayhead" aria-live="polite"><div class="k"><span class="rhk"></span><i></i><span class="rhk2"></span></div><h2 class="rht"></h2><p class="n rhn"></p><div class="m rhm"></div></section>
<nav class="bx-raynav"><button class="pn prev"></button><button class="x close">Esc · Back</button><button class="pn next"></button></nav>`;
  ov.append(root);
  const q = (s: string) => root.querySelector(s) as HTMLElement;
  cv = q("canvas") as HTMLCanvasElement; c = cv.getContext("2d", { alpha: false })!;
  els = { core: q(".bx-core"), ring: q(".ring"), readout: q(".bx-readout"), legend: q(".bx-legend"), rayhead: q(".bx-rayhead"), rhk: q(".rhk"), rhk2: q(".rhk2"), rht: q(".rht"), rhn: q(".rhn"), rhm: q(".rhm"), prev: q(".prev"), next: q(".next"), close: q(".close") };
  els.close.addEventListener("click", () => leave());
  els.prev.addEventListener("click", () => openRay((st.sel + groups.length - 1) % groups.length, true));
  els.next.addEventListener("click", () => openRay((st.sel + 1) % groups.length, true));
}

/* ------------------------------------------------------------------ mode */
const mode: Mode = {
  id: "burst", name: "Burst", background: BG, theme: "light",
  async enter(ctx) {
    const my = ++gen;
    cleanups.forEach((f) => f()); cleanups = [];
    ctxM = ctx; RM = ctx.reducedMotion; SPIN = RM ? .0016 : TAU / (9 * 60);
    ready = false; clockT = 0; coreTf = ringTf = ""; rdT = 0;
    Object.assign(st, { mode: "orbit", sel: -1, hover: -1, lhover: -1, ihover: -1, rot: -.32, rv: 0, rotT: 0, cx: 0, cy: 0, cxv: 0, cyv: 0, Z: 1, Zv: 0, mx: -9999, my: -9999, lx: -9999, ly: -9999, T: 0 });
    document.body.classList.add("bx-on"); focusId = -1;
    ctx.posters.mesh.visible = false; ctx.posters.uniforms.uOpacityMul.value = 0;
    ctx.post.set({ grain: 0, vignette: 0, bloom: 0, aberration: 0 }, 0.01);
    buildDom(ctx.overlay);
    build(ctx.world);
    centerCell = PROFILE.centerId;
    buildLegend(); resize();
    if (document.fonts) { document.fonts.load('500 40px "Host Grotesk"').catch(() => {}); document.fonts.load('10px "Fragment Mono"').catch(() => {}); }
    await buildAtlas(ctx);
    if (my !== gen) return;
    ready = true;
    const hash = bootUsed ? "" : decodeURIComponent(BOOT_HASH.slice(1)); bootUsed = true;
    requestAnimationFrame(() => root.classList.add("on"));
    if (hash.startsWith("ray-")) { const gi = groups.findIndex((G) => String(G.id) === hash.slice(4)); if (gi >= 0) { openRay(gi); for (let t = 0; t < 2.6; t += 1 / 120) step(1 / 120); } }

    const onMove = (e: PointerEvent) => { st.mx = e.clientX; st.my = e.clientY; if (st.lx < -9000) { st.lx = st.mx; st.ly = st.my; } };
    const onLeave = () => { st.mx = st.my = -9999; };
    addEventListener("pointermove", onMove, { passive: true });
    document.addEventListener("pointerleave", onLeave);
    cleanups.push(() => removeEventListener("pointermove", onMove), () => document.removeEventListener("pointerleave", onLeave));
    (window as any).__burst = { open: (i: number) => openRay(i, true), close: () => closeRay(true), st, groups: () => groups };
  },
  update(_ctx, dt) {
    if (!ready || !c) return;
    dt = Math.min(.2, dt); clockT += dt;
    const tw = clockT; for (const G of groups) { const p = clamp((tw - G.delay) / (RM ? .5 : 1.15), 0, 1), q = p - 1; G.grow = 1 + 2.2 * q * q * q + 1.2 * q * q; }
    let rem = dt; while (rem > 1e-6) { const h = Math.min(rem, 1 / 120); step(h); rem -= h; }
    const cvs = ctxM.renderer.domElement;
    if (st.mode !== "ray") { const g = groupAt(st.lx, st.ly); if (g !== st.hover) { st.hover = g; legendSync(); } st.ihover = -1; cvs.style.cursor = g >= 0 ? "pointer" : "default"; }
    else { st.ihover = itemAt(st.mx, st.my); const g = groupAt(st.mx, st.my); cvs.style.cursor = (g >= 0 || st.ihover >= 0) ? "pointer" : "default"; }
    draw(dt);
    const k = S * st.Z, tf = `translate(${(st.cx - 186 * k).toFixed(1)}px,${(st.cy - 186 * k).toFixed(1)}px) scale(${k.toFixed(4)})`;
    if (tf !== coreTf) { els.core.style.transform = tf; coreTf = tf; }
    const rf = `rotate(${(st.rot * 180 / Math.PI).toFixed(2)}deg)`; if (rf !== ringTf) { els.ring.style.transform = rf; ringTf = rf; }
    rdT -= dt; if (rdT <= 0) { rdT = .12; const deg = ((st.rot * 180 / Math.PI) % 360 + 360) % 360;
      els.readout.innerHTML = `θ <b>${deg.toFixed(2)}°</b> · 1 rev / 9 min<br>1 tile ≈ <b>${(totalLogs / tiles.length).toFixed(2)} logs</b> of attention<br>Hover a ray · click to unfold`; }
  },
  focus(ctx, id) {
    if (!ready) return;
    const n = ctx.world.nodes[id]; if (!n || id >= ctx.world.mineCount || n.cluster == null) return;
    const gi = groups.findIndex((G) => G.id === n.cluster); if (gi < 0) return;
    const G = groups[gi];
    focusId = id; ensureItem(G, id);
    if (st.mode === "ray" && st.sel === gi) { placeItems(G); G.items.forEach((it, i) => { if (it.tgt < 1) { it.tgt = 1; it.del = .1 + i * .02; } }); }
    else { st.mode === "ray" && groups[st.sel].items.forEach((it) => { it.tgt = 0; it.del = 0; }); st.mode = "orbit"; openRay(gi); }
  },
  unfocus() { focusId = -1; if (st.mode === "ray") closeRay(true); },
  pointer(_ctx, e, phase) {
    if (phase === "down") downAt = { x: e.clientX, y: e.clientY };
    else if (phase === "up" && downAt) {
      const moved = Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 5; downAt = null;
      if (!moved && ready) {
        const x = e.clientX, y = e.clientY;
        if (st.mode !== "ray") { const g = groupAt(x, y); if (g >= 0) openRay(g, true); }
        else if (itemAt(x, y) < 0) { const g = groupAt(x, y); if (g >= 0) openRay(g, true); else leave(); }
      }
    }
    return true;
  },
  wheel() { return true; },
  key(_ctx, e) {
    if (e.key === "Escape" && st.mode === "ray") { leave(); return true; }
    if (st.mode === "ray" && (e.key === "ArrowRight" || e.key === "ArrowLeft")) { const n = groups.length; openRay((st.sel + (e.key === "ArrowRight" ? 1 : n - 1)) % n, true); return true; }
    return false;
  },
  resize() { if (cv) resize(); },
  exit(ctx) {
    gen++;
    cleanups.forEach((f) => f()); cleanups = [];
    ready = false; ctx.renderer.domElement.style.cursor = "";
    ctx.posters.mesh.visible = true; ctx.posters.uniforms.uOpacityMul.value = 1; document.body.classList.remove("bx-on");
    delete (window as any).__burst;
  },
};
export default mode;
