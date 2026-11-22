/* Burst-style captions pinned to posters (DESIGN.md v3). A DOM layer: every frame each live caption
   is placed from its poster's current on-screen rect (4 projected corners, so tilted posters and
   ghosts work), chased by a light critically damped spring, and written as a transform only.
   Writes are skipped when nothing moved; no layout reads happen per frame. */
import * as THREE from "three";
import type { Captions, TitleNode, WorldData } from "./types";
import type { PosterEngine } from "./posters";

type Side = "below" | "above" | "right" | "left";
interface Cap {
  id: number; el: HTMLElement; side: Side; big: boolean;
  x: number; y: number; vx: number; vy: number; placed: boolean;
  appear: number; delay: number; want: number; // 0..1 fade/slide progress, and target (0 = hiding)
  w: number; lastT: string; lastO: number; lastW: number;
  ghost: THREE.Object3D | null;
}

const GAP = 9;

function lines(n: TitleNode): string[] {
  const out: string[] = [];
  const a = [n.year ? String(n.year) : "", n.maker].filter(Boolean).join(" · ");
  if (a) out.push(a);
  if (n.mine && n.logs) {
    const b: string[] = [];
    if (n.rating != null) b.push("★ " + (n.rating / 2).toFixed(1));
    b.push(`${n.logs} ${n.logs === 1 ? "log" : "logs"}`);
    out.push(b.join(" · "));
  }
  return out;
}

export function createCaptions(root: HTMLElement, world: WorldData, posters: PosterEngine, scene: THREE.Scene, camera: THREE.PerspectiveCamera) {
  const layer = document.createElement("div");
  layer.className = "caps";
  root.append(layer);
  const caps = new Map<number, Cap>();
  let lastShow = -9, burst = 0;
  const now = () => performance.now() / 1000;

  const _p = new THREE.Vector3(), _q = new THREE.Quaternion(), _c = new THREE.Vector3(), _v = new THREE.Vector3();
  const corners = [[-1, -1], [1, -1], [-1, 1], [1, 1]];
  const rect = { l: 0, t: 0, r: 0, b: 0, op: 1, ok: false };

  function ghostMesh(c: Cap): THREE.Object3D | null {
    if (c.ghost && c.ghost.parent && c.ghost.userData.nodeId === c.id) return c.ghost;
    c.ghost = scene.children.find((o) => o.userData.nodeId === c.id) ?? null;
    return c.ghost;
  }

  function measure(c: Cap) {
    let h: number, asp: number;
    if (c.id < world.mineCount) {
      const p = posters.poseOf(c.id, _p, _q);
      h = p.scale; asp = p.aspect; rect.op = p.opacity;
    } else {
      const g = ghostMesh(c);
      if (!g) { rect.ok = false; return; }
      _p.copy(g.position); _q.copy(g.quaternion);
      h = g.scale.y; asp = g.scale.x / g.scale.y;
      const m = (g as THREE.Mesh).material as THREE.ShaderMaterial;
      rect.op = m.uniforms?.uAlpha ? m.uniforms.uAlpha.value : 1;
    }
    const W = innerWidth, H = innerHeight;
    let l = Infinity, t = Infinity, r = -Infinity, b = -Infinity;
    for (const [sx, sy] of corners) {
      _c.set(sx * 0.5 * h * asp, sy * 0.5 * h, 0).applyQuaternion(_q).add(_p);
      _v.copy(_c).applyMatrix4(camera.matrixWorldInverse);
      if (_v.z > -0.1) { rect.ok = false; return; }
      _c.project(camera);
      const x = (_c.x * 0.5 + 0.5) * W, y = (-_c.y * 0.5 + 0.5) * H;
      if (x < l) l = x; if (x > r) r = x; if (y < t) t = y; if (y > b) b = y;
    }
    rect.l = l; rect.t = t; rect.r = r; rect.b = b;
    rect.ok = r > -40 && l < W + 40 && b > -40 && t < H + 40;
  }

  function build(c: Cap, n: TitleNode, index?: string) {
    c.el.replaceChildren();
    const t = document.createElement("div"); t.className = "t";
    if (index) { const i = document.createElement("span"); i.className = "i"; i.textContent = index; t.append(i); }
    t.append(n.title);
    c.el.append(t);
    for (const l of lines(n)) { const m = document.createElement("div"); m.className = "m"; m.textContent = l; c.el.append(m); }
  }

  const api: Captions & { update(dt: number): void } = {
    show(id, opts = {}) {
      const n = world.nodes[id]; if (!n) return;
      const side = opts.side ?? "below", big = !!opts.emphasis;
      let c = caps.get(id);
      const t = now();
      if (!c) {
        burst = t - lastShow > 0.3 ? 0 : burst + 1;
        const el = document.createElement("div");
        layer.append(el);
        c = { id, el, side, big, x: 0, y: 0, vx: 0, vy: 0, placed: false, appear: 0, delay: Math.min(burst, 10) * 0.06 + (big ? 0.15 : 0), want: 1, w: 0, lastT: "", lastO: -1, lastW: -1, ghost: null };
        caps.set(id, c);
        build(c, n, opts.index);
      } else {
        if (c.side !== side || c.big !== big) { c.placed = false; c.vx = c.vy = 0; }
        if (c.want === 0) c.delay = 0;
        if (opts.index !== undefined) build(c, n, opts.index);
      }
      lastShow = t;
      c.side = side; c.big = big; c.want = 1;
      c.el.className = `cap ${side}${big ? " big" : ""}`;
    },
    hide(id) { const c = caps.get(id); if (c) c.want = 0; },
    clear() { for (const c of caps.values()) { c.want = 0; c.delay = 0; } },
    update(dt) {
      if (!caps.size) return;
      dt = Math.min(dt, 0.05);
      for (const c of caps.values()) {
        measure(c);
        const live = rect.ok && rect.op > 0.04;
        // fade/slide progress
        if (c.want && c.delay > 0) c.delay -= dt;
        else c.appear += ((c.want && live ? 1 : 0) - c.appear) * (1 - Math.exp(-dt * (c.want ? 7 : 12)));
        if (!c.want && c.appear < 0.02) { c.el.remove(); caps.delete(c.id); continue; }
        if (!rect.ok) { if (c.lastO !== 0) { c.el.style.opacity = "0"; c.lastO = 0; } continue; }
        // target position by side
        const wBase = c.big ? 240 : 84;
        let w: number, tx: number, ty: number;
        const pw = rect.r - rect.l;
        switch (c.side) {
          case "above": w = Math.max(pw, wBase); tx = rect.l; ty = rect.t - GAP; break;
          case "right": w = c.big ? 280 : 170; tx = rect.r + GAP; ty = rect.t; break;
          case "left": w = c.big ? 280 : 170; tx = rect.l - GAP; ty = rect.t; break;
          default: w = Math.max(pw, wBase); tx = rect.l; ty = rect.b + GAP;
        }
        w = Math.round(w / 4) * 4;
        if (w !== c.lastW) { c.el.style.width = w + "px"; c.lastW = w; }
        const e = c.appear, off = (1 - e) * 10;
        if (!c.placed) { c.x = tx; c.y = ty; c.vx = c.vy = 0; c.placed = true; }
        else { // light critically damped spring (w = 34 rad/s), sub-stepped
          let rem = dt;
          while (rem > 0) {
            const h = Math.min(rem, 1 / 120); rem -= h;
            c.vx += (1156 * (tx - c.x) - 68 * c.vx) * h; c.vy += (1156 * (ty - c.y) - 68 * c.vy) * h;
            c.x += c.vx * h; c.y += c.vy * h;
          }
        }
        const ox = c.side === "right" ? off : c.side === "left" ? -off : 0;
        const oy = c.side === "below" ? off : c.side === "above" ? -off : 0;
        const X = Math.round((c.x + ox) * 2) / 2, Y = Math.round((c.y + oy) * 2) / 2;
        const tr = `translate3d(${X}px,${Y}px,0)` + (c.side === "above" ? " translateY(-100%)" : c.side === "left" ? " translateX(-100%)" : "");
        if (tr !== c.lastT) { c.el.style.transform = tr; c.lastT = tr; }
        const op = Math.round(e * Math.min(1, rect.op * 1.6) * 100) / 100;
        if (op !== c.lastO) { c.el.style.opacity = String(op); c.lastO = op; }
      }
    },
  };
  return api;
}
