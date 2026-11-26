/* TUNNEL — history in depth. His 990 posters line the walls, floor and ceiling of a
   corridor ordered by the day he last logged them (newest nearest). Far posters fade into
   black. Posters only; the one piece of type is the year readout at the screen edge. */
import * as THREE from "three";
import gsap from "gsap";
import type { Ghost, Mode, ModeContext, PoseBuffer } from "../engine/types";

// ---- corridor geometry ----
// A wide tube, 6.4 x 3.2: a wall is one poster tall, floor and ceiling are two posters across,
// one slice every PITCH, all posters one width with an even 0.2 gap.
const W = 3.3, H = 1.7;          // half width / half height (posters hang 0.1 inside)
const GAP = 0.2;
const POSTER_H = 3.1;            // wall height; floor/ceiling: 2 * 3.1 + 0.2 = 6.4
const POSTER_W = POSTER_H * 2 / 3;
const PITCH = POSTER_W + GAP;    // z per slice of posters
const LAT = (POSTER_H + GAP) / 2;
const NEAR0 = 3.5;               // camera starts at z = 3.5: the first slice sits 6 ahead
const Z0 = 2.5;                  // first slice depth
const SLOTS_PER_SLICE = 6;       // 1 on each wall, 2 on the floor, 2 on the ceiling

const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
function rng(seed: number) { let a = seed >>> 0; return () => { a = (a + 0x6d2b79f5) >>> 0; let t = a; t = Math.imul(t ^ (t >>> 15), t | 1); t ^= t + Math.imul(t ^ (t >>> 7), t | 61); return ((t ^ (t >>> 14)) >>> 0) / 4294967296; }; }
function hex(c: string | null): [number, number, number] {
  if (!c) return [0.4, 0.4, 0.4];
  const n = parseInt(c.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
}
const qRotY = (a: number) => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), a);
const qRotX = (a: number) => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), a);
const qRotZ = (a: number) => new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 0, 1), a);
// wall 0 left, 1 right, 2 floor, 3 ceiling
// floor and ceiling posters are turned a quarter so their width runs along the tube like the walls'
const WALL_Q = [qRotY(Math.PI / 2), qRotY(-Math.PI / 2), qRotX(-Math.PI / 2).multiply(qRotZ(-Math.PI / 2)), qRotX(Math.PI / 2).multiply(qRotZ(-Math.PI / 2))];
const WALL_N = [new THREE.Vector3(1, 0, 0), new THREE.Vector3(-1, 0, 0), new THREE.Vector3(0, 1, 0), new THREE.Vector3(0, -1, 0)];
function wallPoint(w: number, lat: number, z: number, out: THREE.Vector3) {
  if (w === 0) return out.set(-W, lat, z);
  if (w === 1) return out.set(W, lat, z);
  if (w === 2) return out.set(lat, -H, z);
  return out.set(lat, H, z);
}

interface Rec {            // a poster (or ghost) taken off the wall onto the stage
  id: number; mine: boolean; e: number;
  p0: THREE.Vector3; p1: THREE.Vector3; q0: THREE.Quaternion; q1: THREE.Quaternion; s0: number; s1: number; m: number;
  ghost?: Ghost; leaving?: boolean; fading?: boolean; fo: number; delay: number;
  pos: THREE.Vector3; quat: THREE.Quaternion; scale: number;
}
const F_SANS = '"Host Grotesk","Helvetica Neue",Arial,sans-serif';
const F_MONO = '"Fragment Mono",Menlo,monospace';

const mode: Mode = (() => {
  let order: number[] = [];
  const nearD = new Float32Array(4), nearI = new Int32Array(4).fill(-1); let nearKey = "";
  let wallFace: Uint8Array, sOpen = 0;
  let wallPos: Float32Array, wallQ: Float32Array, wallScale: Float32Array, wallN: Float32Array, wallZ: Float32Array;
  let L = 100;                                    // depth of the oldest poster
  let s = -3, vExtra = 0;                         // travelled distance, extra velocity
  const lean = new THREE.Vector2(), leanS = new THREE.Vector2();
  let speedS = 0, prevCamZ = 0, arrive = 0;
  let sliceYear: number[] = [];
  const yearCount = new Map<number, number>();
  let readout: HTMLDivElement | null = null, shownYear = -1;
  const recs = new Map<number, Rec>();
  const dimAmt = { v: 0 };
  const camFrom = { pos: new THREE.Vector3(), look: new THREE.Vector3() };
  const camTo = { pos: new THREE.Vector3(), look: new THREE.Vector3() };
  const camW = { v: 0 };
  let focusedNow = false;
  let focusId: number | null = null;
  let stageZ = 0;
  let capToken = 0;
  let dragging = false, dragMoved = false, lastY = 0;
  let pose: PoseBuffer;
  const tmpV = new THREE.Vector3(), tmpQ = new THREE.Quaternion();
  const liveCam = { pos: new THREE.Vector3(), look: new THREE.Vector3() };
  let baseFov = 55, origFov = 45;

  function build(ctx: ModeContext) {
    const { mine } = ctx;
    const n = mine.length;
    // time order: newest first, undated last
    order = mine.map((_, i) => i).sort((a, b) => (mine[b].last || "0").localeCompare(mine[a].last || "0"));
    wallPos = new Float32Array(n * 3); wallQ = new Float32Array(n * 4); wallScale = new Float32Array(n);
    wallN = new Float32Array(n * 3); wallZ = new Float32Array(n); wallFace = new Uint8Array(n);
    // one lap around the tube per slice, in order: left wall, ceiling, right wall, floor
    const slots: [number, number][] = [[0, 0], [3, -LAT], [3, LAT], [1, 0], [2, LAT], [2, -LAT]];
    const slice = (k: number) => Math.floor(k / SLOTS_PER_SLICE);
    sliceYear = []; yearCount.clear();
    for (let k = 0; k < n; k++) {
      const sl = slice(k);
      const id = order[k];
      const [w, lat0] = slots[k % SLOTS_PER_SLICE];
      const node = mine[id];
      const y = node.last ? +node.last.slice(0, 4) : 0;
      if (k % SLOTS_PER_SLICE === 0) sliceYear[sl] = y;
      if (y) yearCount.set(y, (yearCount.get(y) || 0) + 1);
      const hgt = node.type === "music" ? POSTER_W : POSTER_H; // one column width: albums are as wide as films
      const z = -(Z0 + sl * PITCH);
      const lat = lat0; // straight rows: no hand-hung jitter
      const inward = 0.1;
      const roll = 0;
      wallPoint(w, lat, z, tmpV).addScaledVector(WALL_N[w], inward);
      wallPos.set([tmpV.x, tmpV.y, tmpV.z], id * 3);
      tmpQ.copy(WALL_Q[w]).multiply(qRotZ(roll));
      wallQ.set([tmpQ.x, tmpQ.y, tmpQ.z, tmpQ.w], id * 4);
      wallScale[id] = hgt;
      wallN.set([WALL_N[w].x, WALL_N[w].y, WALL_N[w].z], id * 3);
      wallZ[id] = z; wallFace[id] = w;
    }
    L = Z0 + (slice(n - 1) + 1) * PITCH;
  }

  // ---- poster poses ----
  const wq = new THREE.Quaternion();
  function recPose(r: Rec) {
    r.pos.lerpVectors(r.p0, r.p1, r.m);
    r.quat.copy(r.q0).slerp(r.q1, r.m);
    r.scale = r.s0 + (r.s1 - r.s0) * r.m;
  }
  function writePoses(ctx: ModeContext) {
    const cam = ctx.rig.camera;
    const camZ = cam.position.z;
    const n = ctx.posters.count;
    const P = pose;
    const dim = dimAmt.v;
    nearD.fill(1e9); nearI.fill(-1);
    for (let i = 0; i < n; i++) {
      const d = camZ - wallZ[i];
      P.position[i * 3] = wallPos[i * 3]; P.position[i * 3 + 1] = wallPos[i * 3 + 1]; P.position[i * 3 + 2] = wallPos[i * 3 + 2];
      P.quaternion[i * 4] = wallQ[i * 4]; P.quaternion[i * 4 + 1] = wallQ[i * 4 + 1]; P.quaternion[i * 4 + 2] = wallQ[i * 4 + 2]; P.quaternion[i * 4 + 3] = wallQ[i * 4 + 3];
      P.scale[i] = wallScale[i];
      P.billboard[i] = 0;
      // walls clear the camera early; floor and ceiling clear the bottom/top of the screen
      // (where the menu and year readout sit) by fading out before they get near
      const face = wallFace[i];
      let near = smooth(3.5, 7, d);
      if (near > 0.001) {
        // keep a clean margin top and bottom (menu and year readout live there): a poster
        // fades out as its lowest / highest corner nears the screen edge
        const zn = wallZ[i] + POSTER_W * 0.5, hh = face < 2 ? wallScale[i] * 0.5 : 0;
        const cy = wallPos[i * 3 + 1];
        const lowY = tmpV.set(wallPos[i * 3], cy - hh, zn).project(cam).y;
        const highY = tmpV.set(wallPos[i * 3], cy + hh, zn).project(cam).y;
        near *= smooth(-0.76, -0.62, lowY) * (1 - smooth(0.62, 0.76, highY));
      }
      const o = near * (1 - smooth(8, 58, d));
      P.opacity[i] = o * (1 - 0.99 * dim);
      if (!focusedNow && o > 0.35 && d < nearD[3]) { let k = 3; while (k > 0 && d < nearD[k - 1]) { nearD[k] = nearD[k - 1]; nearI[k] = nearI[k - 1]; k--; } nearD[k] = d; nearI[k] = i; }
    }
    for (const r of recs.values()) {
      if (!r.mine) continue;
      const i = r.id; recPose(r);
      const ee = r.e * r.e * (3 - 2 * r.e);
      tmpV.set(P.position[i * 3], P.position[i * 3 + 1], P.position[i * 3 + 2]).lerp(r.pos, ee);
      P.position.set([tmpV.x, tmpV.y, tmpV.z], i * 3);
      wq.fromArray(P.quaternion, i * 4).slerp(r.quat, ee);
      P.quaternion.set([wq.x, wq.y, wq.z, wq.w], i * 4);
      P.scale[i] = P.scale[i] + (r.scale - P.scale[i]) * ee;
      // the hero comes up at once; its next titles only show once they are nearly in place,
      // so nothing flies half behind the hero
      const g = i === focusId ? Math.min(1, ee * 2) : smooth(0.5, 1, ee);
      P.opacity[i] = (P.opacity[i] + (1 - P.opacity[i]) * g) * (1 - r.fo);
    }
    ctx.posters.follow(P);
    if (!focusedNow) {   // full-resolution artwork for the nearest (biggest) posters
      const key = nearI.join(",");
      if (key !== nearKey && nearI[0] >= 0) { nearKey = key; ctx.posters.sharp(Array.from(nearI).filter((x) => x >= 0)); }
    }
    for (const [id, r] of recs) {
      if (r.mine) { if (r.leaving && (r.e < 0.002 || r.fo >= 1)) recs.delete(id); continue; }
      if (!r.ghost) continue;
      recPose(r);
      const ee = r.e * r.e * (3 - 2 * r.e);
      r.ghost.mesh.quaternion.copy(r.quat);
      r.ghost.set(r.pos, r.scale, 0.95 * smooth(0.5, 1, ee) * (1 - r.fo));
      if (r.leaving && (r.fo >= 1 || r.e < 0.01)) { r.ghost.remove(); recs.delete(id); }
    }
  }

  // ---- stage: the focused poster and its next titles ----
  function makeRec(id: number, mine: boolean, from: THREE.Vector3, q: THREE.Quaternion, sc: number, delay: number, ctx: ModeContext): Rec {
    const r: Rec = { id, mine, fo: 0, e: 0, p0: from.clone(), p1: from.clone(), q0: q.clone(), q1: q.clone(), s0: sc, s1: sc, m: 1, delay, pos: from.clone(), quat: q.clone(), scale: sc };
    if (!mine) r.ghost = ctx.posters.ghost(id, from, { scale: sc, opacity: 0, quaternion: q.clone(), billboard: false });
    return r;
  }
  function place(r: Rec, p: THREE.Vector3, q: THREE.Quaternion, sc: number) {
    recPose(r);
    r.p0.copy(r.pos); r.q0.copy(r.quat); r.s0 = r.scale;
    r.p1.copy(p); r.q1.copy(q); r.s1 = sc; r.m = 0;
    gsap.killTweensOf(r);
    gsap.to(r, { m: 1, duration: 1.5, delay: r.delay, ease: "expo.inOut" });
    r.leaving = false; r.fading = false; r.fo = 0;
    gsap.to(r, { e: 1, duration: 1.5, delay: r.delay, ease: "expo.inOut" });
  }
  // fade = true: dissolve where it hangs (travel); false: fly back into its slot (Esc)
  function leave(r: Rec, fade: boolean) {
    r.leaving = true; r.fading = fade;
    gsap.killTweensOf(r);
    if (fade || !r.mine) gsap.to(r, { fo: 1, duration: 0.7, ease: "power2.inOut" });
    else gsap.to(r, { e: 0, duration: 1.3, ease: "expo.inOut" });
  }

  const STAGE_D = 11, STAGE_FOV = 32;
  function focusOn(ctx: ModeContext, id: number) {
    const node = ctx.world.nodes[id];
    const first = !focusedNow;
    if (first) sOpen = s;
    const aspect = ctx.size.width / ctx.size.height;
    const wide = aspect > 1.15;
    const existing = recs.get(id);
    let zf: number;
    if (existing && !existing.leaving) zf = existing.p1.z;
    else if (node.mine) zf = wallZ[id];
    else zf = (first ? liveCam.pos.z : stageZ) - 9;
    stageZ = zf;
    const nextIds = node.next.slice(0, 5);
    const keep = new Set<number>([id, ...nextIds]);
    for (const [rid, r] of recs) if (!keep.has(rid)) leave(r, !first || !r.mine);

    // stage geometry in the camera's own plane: camera looks straight down -z from zf + D
    const vh = 2 * STAGE_D * Math.tan((STAGE_FOV * Math.PI) / 360), vw = vh * aspect;
    const place1 = (pid: number, p: THREE.Vector3, sc: number, delay: number) => {
      const nd = ctx.world.nodes[pid];
      const q = new THREE.Quaternion();
      let r = recs.get(pid);
      if (!r) {
        const from = nd.mine ? new THREE.Vector3(wallPos[pid * 3], wallPos[pid * 3 + 1], wallPos[pid * 3 + 2]) : p.clone().add(new THREE.Vector3(0, 0, -3));
        const fq = nd.mine ? new THREE.Quaternion().fromArray(wallQ, pid * 4) : q.clone();
        r = makeRec(pid, nd.mine, from, fq, nd.mine ? wallScale[pid] : sc * 0.8, delay, ctx);
        recs.set(pid, r);
      } else r.delay = delay;
      place(r, p, q, sc);
    };
    const layout: { id: number; x: number; y: number; h: number; side: "below"; emph: boolean; idx?: string }[] = [];
    if (wide) {
      const hh = vh * 0.6;
      layout.push({ id, x: -vw * 0.24, y: vh * 0.07, h: hh, side: "below", emph: true });
      const nh = vh * 0.27, cx = [0.07, 0.24, 0.41], rowY = [vh * 0.2, -vh * 0.24];
      nextIds.forEach((nid, k) => layout.push({ id: nid, x: vw * (k < 3 ? cx[k] : cx[k - 3] + 0.085), y: rowY[k < 3 ? 0 : 1], h: nh, side: "below", emph: false, idx: "0" + (k + 1) }));
    } else {
      layout.push({ id, x: 0, y: vh * 0.22, h: vh * 0.36, side: "below", emph: true });
      const nh = vh * 0.15, cx = [-0.27, 0, 0.27], rowY = [-vh * 0.2, -vh * 0.38];
      nextIds.forEach((nid, k) => layout.push({ id: nid, x: vw * (k < 3 ? cx[k] : cx[k - 3] + 0.135), y: rowY[k < 3 ? 0 : 1], h: nh, side: "below", emph: false, idx: "0" + (k + 1) }));
    }
    layout.forEach((it, k) => place1(it.id, new THREE.Vector3(it.x, it.y, 0).add(tmpV.set(0, 0, zf)), it.h, k === 0 ? 0 : 0.12 + (k - 1) * 0.07));
    // camera: from where it is now to the stage
    camFrom.pos.copy(ctx.rig.camera.position);
    camFrom.look.copy(first ? liveCam.look : ctx.rig.lookAt);
    camTo.pos.set(0, 0, zf + STAGE_D);
    camTo.look.set(0, 0, zf - 5);
    camW.v = 0; gsap.killTweensOf(camW);
    const dist = Math.abs(camFrom.pos.z - camTo.pos.z);
    gsap.to(camW, { v: 1, duration: Math.min(3.0, Math.max(1.5, 1.2 + dist * 0.014)), ease: "power3.inOut" });
    gsap.killTweensOf(dimAmt);
    gsap.to(dimAmt, { v: 1, duration: 1.2, ease: "power2.out" });
    focusedNow = true; focusId = id;
    ctx.posters.sharp([id, ...nextIds.filter((n) => ctx.world.nodes[n].mine)]);
    // captions once the posters have mostly arrived
    ctx.captions.clear();
    const tok = ++capToken;
    gsap.delayedCall(1.1, () => {
      if (tok !== capToken || !focusedNow) return;
      for (const it of layout) ctx.captions.show(it.id, { side: it.side, index: it.idx, emphasis: it.emph });
    });
  }

  function computeLive(dt: number) {
    leanS.lerp(lean, 1 - Math.pow(0.04, dt));
    const camZ = -s;
    liveCam.pos.set(leanS.x * 0.55, -leanS.y * 0.38, camZ);
    liveCam.look.set(leanS.x * 3.0, -leanS.y * 1.9, camZ - 14);
  }

  return {
    id: "tunnel", name: "Tunnel", theme: "dark", background: "#020203",
    async enter(ctx) {
      recs.clear();
      vExtra = 0; s = -NEAR0; focusedNow = false; focusId = null; dimAmt.v = 0; camW.v = 0; arrive = 14; shownYear = -1;
      build(ctx);
      if (!document.getElementById("tn-fonts")) {
        const l = document.createElement("link"); l.id = "tn-fonts"; l.rel = "stylesheet";
        l.href = "https://fonts.googleapis.com/css2?family=Host+Grotesk:wght@300..700&family=Fragment+Mono&display=swap"; document.head.append(l);
      }
      readout = document.createElement("div");
      readout.style.cssText = `position:absolute;right:28px;bottom:58px;text-align:right;pointer-events:none;font:9px/1.75 ${F_MONO};letter-spacing:.09em;text-transform:uppercase;color:rgba(242,239,232,.5);transition:opacity .6s`;
      ctx.overlay.append(readout);
      pose = ctx.posters.makePose();
      ctx.rig.damping = 0.085;
      origFov = ctx.rig.camera.fov; baseFov = 55; ctx.rig.parallax = 0; ctx.rig.sway = 0;
      computeLive(0.016);
      prevCamZ = ctx.rig.camera.position.z;
      writePoses(ctx);
      ctx.posters.morphTo(pose, { duration: 1.9, stagger: 0.9, ease: "inOutExpo" });
      const st = { z: 14 };
      gsap.to(st, { z: 0, duration: 2.8, ease: "expo.out", onUpdate: () => { arrive = st.z; } });
    },
    update(ctx, dt) {
      const cam = ctx.rig.camera;
      if (!focusedNow && camW.v <= 0.0005) {
        const drift = ctx.reducedMotion ? 0.25 : 0.95;
        vExtra *= Math.exp(-dt * 1.45);
        const lo = -NEAR0, hi = L - 9;     // stop with the last posters still ahead, then rest
        s += (drift * (1 - smooth(hi - 14, hi, s)) + vExtra) * dt;
        if (s < lo) { s += (lo - s) * Math.min(1, dt * 6); vExtra = Math.max(vExtra, 0); }
        if (s > hi) { s += (hi - s) * Math.min(1, dt * 3); vExtra = Math.min(vExtra, 0); }
      } else vExtra = 0;
      computeLive(dt);
      const e = camW.v;
      if (focusedNow) {
        ctx.rig.position.lerpVectors(camFrom.pos, camTo.pos, e);
        ctx.rig.lookAt.lerpVectors(camFrom.look, camTo.look, e);
        ctx.rig.position.x += leanS.x * 0.1 * e; ctx.rig.position.y += -leanS.y * 0.07 * e;
      } else if (e > 0.0005) {
        ctx.rig.position.lerpVectors(liveCam.pos, camTo.pos, e);
        ctx.rig.lookAt.lerpVectors(liveCam.look, camTo.look, e);
      } else {
        ctx.rig.position.copy(liveCam.pos); ctx.rig.lookAt.copy(liveCam.look);
        ctx.rig.position.z += arrive;
      }
      const camZ = cam.position.z;
      const sp = Math.abs(camZ - prevCamZ) / Math.max(dt, 1e-3); prevCamZ = camZ;
      speedS += (Math.min(sp, 120) - speedS) * Math.min(1, dt * 5);
      const fov = focusedNow ? STAGE_FOV : baseFov;
      if (Math.abs(cam.fov - fov) > 0.01) { cam.fov += (fov - cam.fov) * Math.min(1, dt * 6); cam.updateProjectionMatrix(); }
      if (readout) {
        const sl = Math.max(0, Math.min(sliceYear.length - 1, Math.round((s + 6 - Z0) / PITCH)));
        const y = sliceYear[sl] || 0;
        if (y !== shownYear) {
          shownYear = y;
          readout.innerHTML = y ? `<span style="color:#F2EFE8">${y}</span> · ${yearCount.get(y) || 0} titles` : "";
        }
        readout.style.opacity = focusedNow ? "0" : "1";
      }
      writePoses(ctx);
    },
    focus(ctx, id) { focusOn(ctx, id); },
    unfocus(ctx) {
      focusedNow = false; focusId = null; capToken++; s = sOpen; vExtra = 0; nearKey = "";
      ctx.captions.clear();
      for (const r of recs.values()) leave(r, false);
      gsap.killTweensOf(camW); gsap.to(camW, { v: 0, duration: 1.7, ease: "power3.inOut" });
      gsap.killTweensOf(dimAmt); gsap.to(dimAmt, { v: 0, duration: 1.4, ease: "power2.inOut" });
    },
    pointer(ctx, e, phase) {
      /* no pointer lean: the camera only moves when you fly */
      if (phase === "down") { dragging = true; dragMoved = false; lastY = e.clientY; return false; }
      if (phase === "move") {
        if (!dragging || focusedNow) return false;
        const dy = e.clientY - lastY; lastY = e.clientY;
        if (dy !== 0) { dragMoved = dragMoved || Math.abs(dy) > 1; vExtra = Math.max(-80, Math.min(80, vExtra + dy * 0.55)); }
        return dragMoved;
      }
      dragging = false; const used = dragMoved; dragMoved = false;
      return used;
    },
    wheel(_ctx, e) {
      if (focusedNow) return true;
      const dy = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY;
      vExtra = Math.max(-80, Math.min(80, vExtra + Math.max(-150, Math.min(150, dy)) * 0.05));
      return true;
    },
    key(_ctx, e) {
      if (focusedNow) return false;
      if (e.key === "ArrowUp" || e.key === "ArrowRight" || e.key === "w") { vExtra = Math.min(80, vExtra + 7); return true; }
      if (e.key === "ArrowDown" || e.key === "ArrowLeft" || e.key === "s") { vExtra = Math.max(-80, vExtra - 7); return true; }
      if (e.key === " ") { vExtra = Math.min(80, vExtra + 22); e.preventDefault(); return true; }
      return false;
    },
    resize(ctx) { if (focusedNow && focusId != null) focusOn(ctx, focusId); },
    exit(ctx) {
      capToken++;
      gsap.killTweensOf(camW); gsap.killTweensOf(dimAmt);
      for (const r of recs.values()) { gsap.killTweensOf(r); r.ghost?.remove(); }
      recs.clear();
      ctx.captions.clear();
      ctx.posters.clearGhosts();
      readout = null;
      ctx.rig.camera.fov = origFov; ctx.rig.parallax = 0; ctx.rig.sway = 0; ctx.rig.camera.updateProjectionMatrix();
      ctx.rig.damping = 0.12;
    },
  } as Mode;
})();

export default mode;
