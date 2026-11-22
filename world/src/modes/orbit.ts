/* ORBIT (v3 clean-up): you are the centre. His 990 titles sit on a tilted disc in taste sectors
   (angle = cluster) and revolve slowly; the more recently a title was logged, the closer to the
   centre and the bigger its poster. Nothing but posters on the disc: spacing does the work.
   Click a title: it comes forward, its 5 next titles line up beside it, the disc dims behind. */
import * as THREE from "three";
import type { Ghost, Mode, ModeContext, PoseBuffer } from "../engine/types";

const TAU = Math.PI * 2;
const INK = "242,239,232";
const F_SANS = '"Host Grotesk","Schibsted Grotesk","Helvetica Neue",Arial,sans-serif';
const F_MONO = '"Fragment Mono","IBM Plex Mono",Menlo,monospace';

const R_IN = 0, R_SPAN = 26, S_IN = 3.0, S_OUT = 1.9;
const PITCH = 0.95;      // camera elevation (radians above the disc plane)
const GAP = 0;          // angular gap between sectors

const clamp = (x: number, a: number, b: number) => Math.min(b, Math.max(a, x));
const smooth = (a: number, b: number, x: number) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };
const sizeAt = (r: number) => S_IN * Math.pow(S_OUT / S_IN, clamp((r - R_IN) / R_SPAN, 0, 1));
function hash(i: number, k: number) { let h = (i * 374761393 + k * 668265263) | 0; h = Math.imul(h ^ (h >>> 13), 1274126177); h ^= h >>> 16; return (h >>> 0) / 4294967296; }

interface FocusItem { id: number; x: number; y: number; h: number; ghost: Ghost | null; pos: THREE.Vector3; born: number }

export default (function orbitMode(): Mode {
  let ctx!: ModeContext;
  let n = 0;
  const baseR = new Float32Array(990), baseA = new Float32Array(990), baseS = new Float32Array(990);
  let rMax = 30;
  let live: PoseBuffer;
  let centre: number | null = null;
  let items: FocusItem[] = [];
  const itemById = new Map<number, FocusItem>();

  const cam = { yaw: 0.3, pitch: PITCH, dist: 100 };
  const camT = { yaw: 0.3, pitch: PITCH, dist: 100 };
  let yawVel = 0, pitchVel = 0;
  const drag = { on: false, moved: false, x: 0, y: 0 };
  let ff = 0;        // focus fade 0..1
  let spin = 0;
  let tAbs = 0;
  let seq = 0;
  let swirl0: any;

  let root: HTMLDivElement, hoverEl: HTMLDivElement, escEl: HTMLDivElement;
  let lastHover: number | null | undefined;

  const v = new THREE.Vector3(), fw = new THREE.Vector3(), rt = new THREE.Vector3(), up = new THREE.Vector3(), camPos = new THREE.Vector3(), look = new THREE.Vector3();
  const Y = new THREE.Vector3(0, 1, 0);

  // ---------------------------------------------------------------- the disc layout
  function buildBase() {
    const cl = ctx.world.clusters;
    const groups: number[][] = cl.map((c) => c.members.filter((id) => id < 990));
    const seen = new Set<number>(groups.flat());
    const rest: number[] = []; for (let i = 0; i < 990; i++) if (!seen.has(i)) rest.push(i);
    if (rest.length) groups.push(rest);
    // the newest titles of all tastes fill the centre as a dense sunflower, so the sectors never pinch to a star
    const NC = 30, CC = 1.6;
    const newest = Array.from({ length: 990 }, (_, i) => i).sort((a, b) => (ctx.mine[b].last || "").localeCompare(ctx.mine[a].last || "")).slice(0, NC);
    const inC = new Set(newest);
    newest.forEach((id, k) => {
      const r = CC * Math.sqrt(k + 0.5);
      baseR[id] = r; baseS[id] = sizeAt(r); baseA[id] = k * 2.399963;
    });
    const R0 = CC * Math.sqrt(NC) + 1.5;
    for (let gi2 = 0; gi2 < groups.length; gi2++) groups[gi2] = groups[gi2].filter((id) => !inC.has(id));
    const total = groups.reduce((s, g) => s + g.length, 0);
    const usable = TAU - GAP * groups.length;
    let a0 = -Math.PI / 2; rMax = R0;
    let gi = 0;
    for (const g of groups) {
      const w = (g.length / total) * usable;
      const sorted = g.slice().sort((a, b) => (ctx.mine[b].last || "").localeCompare(ctx.mine[a].last || ""));
      let r = R0 + (gi++ % 2) * 1.6, k = 0, idx = 0;
      while (idx < sorted.length) {
        const S = sizeAt(r);
        const cap = Math.max(1, Math.floor((w * r) / (1.1 * S)));
        const m = Math.min(cap, sorted.length - idx);
        const stag = 0;
        for (let j = 0; j < m; j++) {
          const id = sorted[idx + j];
          baseR[id] = r; baseS[id] = S;
          baseA[id] = a0 + (w * (j + 0.5 + (cap - m) / 2 + stag)) / cap;
        }
        rMax = Math.max(rMax, r);
        idx += m; r += S * 1.28; k++;
      }
      a0 += w + GAP;
    }
  }

  function fitDist() {
    const fov = THREE.MathUtils.degToRad(ctx.rig.camera.fov) / 2;
    const asp = innerWidth / innerHeight;
    const halfV = rMax * Math.sin(PITCH) + 4.2, halfH = rMax + 2.2;
    return Math.max(halfV / Math.tan(fov), halfH / (Math.tan(fov) * asp)) * 1.1;
  }

  // ---------------------------------------------------------------- camera-space helpers
  function basis() {
    const d = cam.dist, yw = cam.yaw, p = cam.pitch;
    camPos.set(d * Math.sin(yw) * Math.cos(p), d * Math.sin(p), d * Math.cos(yw) * Math.cos(p));
    look.set(0, 0, 0);
    fw.copy(look).sub(camPos).normalize();
    rt.crossVectors(fw, Y).normalize();
    up.crossVectors(rt, fw).normalize();
    look.addScaledVector(up, -d * 0.045);   // sits the disc a little high, clear of the mode menu
    fw.copy(look).sub(camPos).normalize();
    rt.crossVectors(fw, Y).normalize();
    up.crossVectors(rt, fw).normalize();
  }
  function layoutItems(ids: number[], prev: Map<number, FocusItem>) {
    const asp = innerWidth / innerHeight;
    const wide = asp > 1.15;
    const out: FocusItem[] = [];
    // coordinates are fractions of the visible height at the focus plane
    const VW = asp;
    ids.forEach((id, k) => {
      let x: number, y: number, h: number;
      if (k === 0) {
        if (wide) { x = -0.24 * VW; y = 0.03; h = 0.5; } else { x = 0; y = 0.17; h = 0.36; }
      } else {
        const i = k - 1;
        if (wide) { h = 0.19; const pitch = Math.min(0.2, (0.4 * VW) / 4); x = 0.2 * VW + (i - 2) * pitch; y = 0.02; }
        else { h = 0.15; const pitch = (0.84 * VW) / 5; x = (i - 2) * pitch; y = -0.26; }
      }
      const old = prev.get(id);
      out.push({ id, x, y, h, ghost: old?.ghost ?? null, pos: old?.pos ?? new THREE.Vector3(), born: old?.born ?? tAbs });
    });
    return out;
  }
  const tanHalf = () => Math.tan(THREE.MathUtils.degToRad(ctx.rig.camera.fov) / 2);
  function itemWorld(it: FocusItem, out: THREE.Vector3) {
    const D = cam.dist * 0.5, VH = 2 * D * tanHalf();
    return out.copy(camPos).addScaledVector(fw, D).addScaledVector(rt, it.x * VH).addScaledVector(up, it.y * VH);
  }
  const itemScale = (it: FocusItem) => it.h * cam.dist * tanHalf();

  // ---------------------------------------------------------------- per-frame pose
  function fillPose() {
    basis();
    const d0 = cam.dist - rMax * 0.5, d1 = cam.dist + rMax * 0.9;
    for (let i = 0; i < n; i++) {
      const a = baseA[i] + spin, R = baseR[i];
      v.set(Math.cos(a) * R, 0, Math.sin(a) * R);
      live.position[i * 3] = v.x; live.position[i * 3 + 1] = v.y; live.position[i * 3 + 2] = v.z;
      const depth = v.sub(camPos).dot(fw);
      let o = 1 - 0.55 * smooth(d0, d1, depth);
      o *= 1 - 0.985 * ff;
      live.opacity[i] = o;
      live.scale[i] = baseS[i];
    }
    for (const it of items) {
      if (it.id >= 990) continue;
      itemWorld(it, v);
      live.position[it.id * 3] = v.x; live.position[it.id * 3 + 1] = v.y; live.position[it.id * 3 + 2] = v.z;
      live.opacity[it.id] = 1; live.scale[it.id] = itemScale(it);
    }
  }

  // ---------------------------------------------------------------- focus relay
  async function relay(id: number | null) {
    seq++;
    const nodes = ctx.world.nodes;
    ctx.captions?.clear();
    const order = new Float32Array(n);
    if (id == null) {
      centre = null;
      for (const it of items) it.ghost?.remove();
      items = []; itemById.clear(); ctx.posters.clearGhosts();
      fillPose();
      for (let i = 0; i < n; i++) order[i] = baseR[i] / R_SPAN;
      await ctx.posters.morphTo(live, { duration: 1.6, stagger: 0.8, ease: "outQuart", order });
      return;
    }
    const prevItems = new Map(itemById);
    centre = id;
    const nxt: number[] = [];
    for (const x of nodes[id].next) if (x !== id && !nxt.includes(x) && nxt.length < 5) nxt.push(x);
    const ids = [id, ...nxt];
    for (const [k, it] of prevItems) if (!ids.includes(k)) { it.ghost?.remove(); it.ghost = null; }
    items = layoutItems(ids, prevItems);
    itemById.clear(); for (const it of items) itemById.set(it.id, it);
    basis();
    const origin = new THREE.Vector3();
    if (prevItems.size) prevItems.values().next().value!.pos && origin.copy(prevItems.values().next().value!.pos);
    for (const it of items) {
      if (it.id >= 990 && !it.ghost) {
        it.pos.copy(origin); it.born = tAbs;
        it.ghost = ctx.posters.ghost(it.id, it.pos, { scale: itemScale(it), opacity: 0, billboard: true });
      }
    }
    ctx.posters.sharp(ids.filter((x) => x < 990));
    ctx.captions?.show(id, { side: "below", emphasis: true });
    nxt.forEach((x, k) => ctx.captions?.show(x, { side: "below", index: String(k + 1).padStart(2, "0") }));
    fillPose();
    for (let i = 0; i < n; i++) order[i] = 0.55 + 0.45 * hash(i, 11);
    items.forEach((it, k) => { if (it.id < 990) order[it.id] = k * 0.06; });
    await ctx.posters.morphTo(live, { duration: 1.5, stagger: 0.7, ease: "outQuart", order });
  }

  // ---------------------------------------------------------------- DOM edge type
  function buildDom() {
    root = document.createElement("div");
    root.style.cssText = `position:absolute;inset:0;pointer-events:none;font-family:${F_SANS};color:rgb(${INK});-webkit-font-smoothing:antialiased`;
    root.innerHTML = `
      <div style="position:absolute;inset:0;background:radial-gradient(ellipse at 50% 50%,rgba(0,0,0,0) 62%,rgba(0,0,0,.28) 100%)"></div>
      <div style="position:absolute;left:28px;top:24px;font:9px/1.6 ${F_MONO};letter-spacing:.1em;text-transform:uppercase;color:rgba(${INK},.45)">Spine World &middot; Orbit</div>
      <div style="position:absolute;right:28px;top:22px;text-align:right"><div style="font:500 13px/1.25 ${F_SANS};letter-spacing:-.005em;color:rgba(${INK},.92)">Armaan</div>
        <div style="font:9px/1.7 ${F_MONO};letter-spacing:.09em;text-transform:uppercase;color:rgba(${INK},.45);margin-top:5px">990 titles &middot; since 2021</div></div>
      <div data-k="hint" style="position:absolute;right:28px;bottom:26px;text-align:right;font:9px/1.75 ${F_MONO};letter-spacing:.09em;text-transform:uppercase;color:rgba(${INK},.45);transition:opacity .5s">Nearer and larger = logged more recently</div>
      <div data-k="hover" style="position:absolute;left:28px;bottom:64px;font:9px/1.75 ${F_MONO};letter-spacing:.09em;text-transform:uppercase;color:rgba(${INK},.45);transition:opacity .4s;opacity:0"></div>
      <div data-k="esc" style="position:absolute;left:28px;bottom:64px;font:9px/1.75 ${F_MONO};letter-spacing:.09em;text-transform:uppercase;color:rgba(${INK},.6);transition:opacity .5s;opacity:0">Esc &middot; back to the disc</div>`;
    ctx.overlay.appendChild(root);
    hoverEl = root.querySelector('[data-k="hover"]') as HTMLDivElement;
    escEl = root.querySelector('[data-k="esc"]') as HTMLDivElement;
  }
  function updateHover() {
    const h = centre == null ? ctx.posters.hovered : null;
    if (h === lastHover) return;
    lastHover = h;
    if (h == null) { hoverEl.style.opacity = "0"; return; }
    const nd = ctx.world.nodes[h];
    const cl = nd.cluster != null ? ctx.world.clusters.find((c) => c.id === nd.cluster)?.name : "";
    hoverEl.innerHTML = `<span style="font:500 13px/1.25 ${F_SANS};letter-spacing:-.005em;text-transform:none;color:rgba(${INK},.92)">${nd.title.replace(/</g, "&lt;")}</span><br>${cl || ""}${nd.last ? " &middot; last logged " + nd.last : ""}`;
    hoverEl.style.opacity = "1";
  }

  // ---------------------------------------------------------------- mode
  return {
    id: "orbit", name: "Orbit", background: "#050505", theme: "dark",

    async enter(c) {
      ctx = c; n = c.posters.count;
      centre = null; items = []; itemById.clear(); ff = 0; spin = 0; tAbs = 0; seq = 0; lastHover = undefined;
      c.rig.camera.fov = 36; c.rig.camera.updateProjectionMatrix();
      c.rig.damping = 0.2; c.rig.parallax = 0; c.rig.sway = 0;
      swirl0 = c.posters.uniforms.uSwirl?.value;
      if (c.posters.uniforms.uSwirl) c.posters.uniforms.uSwirl.value = 0;
      buildBase();
      const d = fitDist();
      Object.assign(cam, { yaw: 0.3, pitch: PITCH, dist: d });
      Object.assign(camT, { yaw: 0.3, pitch: PITCH, dist: d });
      live = c.posters.makePose();
      for (let i = 0; i < n; i++) live.billboard[i] = 1;
      buildDom();
      fillPose();
      const order = new Float32Array(n); for (let i = 0; i < n; i++) order[i] = baseR[i] / R_SPAN;
      c.posters.morphTo(live, { duration: 0.8, stagger: 0.15, ease: "outQuart", order });
    },

    update(c, dt, t) {
      tAbs = t;
      ff += ((centre != null ? 1 : 0) - ff) * (1 - Math.exp(-dt * (centre != null ? 6 : 3)));
      spin += dt * 0.02 * (1 - 0.85 * ff);
      if (!drag.on) {
        camT.yaw += yawVel * dt; camT.pitch = clamp(camT.pitch + pitchVel * dt, 0.5, 1.35);
        const dec = Math.exp(-dt * 2.2); yawVel *= dec; pitchVel *= dec;
      }
      const k = 1 - Math.exp(-dt * 2.6);
      if (drag.on && drag.moved) { cam.yaw = camT.yaw; cam.pitch = camT.pitch; }
      else { cam.yaw += (camT.yaw - cam.yaw) * k; cam.pitch += (camT.pitch - cam.pitch) * k; }
      cam.dist += (camT.dist - cam.dist) * (1 - Math.exp(-dt * 4));
      basis();
      c.rig.lookAt.copy(look);
      c.rig.position.copy(camPos);

      fillPose();
      c.posters.follow(live);

      for (const it of items) {
        if (it.id < 990 || !it.ghost) continue;
        itemWorld(it, v);
        it.pos.lerp(v, 1 - Math.exp(-dt * 4));
        it.ghost.set(it.pos, itemScale(it), smooth(0.2, 1.2, t - it.born));
      }
      escEl.style.opacity = ff > 0.5 ? "1" : "0";
      (root.querySelector('[data-k="hint"]') as HTMLElement).style.opacity = ff > 0.5 ? "0" : "1";
      updateHover();
    },

    focus(_c, id) { if (id !== centre) relay(id); },
    unfocus() { relay(null); },

    pointer(_c, e, phase) {
      if (phase === "down") { drag.on = true; drag.moved = false; drag.x = e.clientX; drag.y = e.clientY; yawVel = pitchVel = 0; return false; }
      if (phase === "move") {
        if (!drag.on) return false;
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) < 4) return false;
        drag.moved = true; drag.x = e.clientX; drag.y = e.clientY;
        camT.yaw -= dx * 0.005; camT.pitch = clamp(camT.pitch + dy * 0.004, 0.5, 1.35);
        yawVel = -dx * 0.1; pitchVel = dy * 0.08;
        return true;
      }
      const was = drag.moved; drag.on = false; return was;
    },
    wheel(_c, e) {
      const d = fitDist();
      camT.dist = clamp(camT.dist * Math.exp(e.deltaY * 0.0012), d * 0.45, d * 1.3);
      return true;
    },
    resize() {
      if (centre == null) camT.dist = fitDist();
      if (items.length) { const prev = new Map(itemById); items = layoutItems(items.map((i) => i.id), prev); itemById.clear(); for (const it of items) itemById.set(it.id, it); }
    },

    exit() {
      seq++;
      if (ctx.posters.uniforms.uSwirl && swirl0 !== undefined) ctx.posters.uniforms.uSwirl.value = swirl0;
      ctx.captions?.clear();
      for (const it of items) it.ghost?.remove();
      items = []; itemById.clear();
      ctx.posters.clearGhosts();
      root?.remove();
    },
  };
})();
