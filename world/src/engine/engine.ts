/* Engine v1: renderer + composer, camera rig, GPU poster field, input, focus, and the
   signature mode-switch (one continuous world reshaping itself). Modes only use types.ts. */
import * as THREE from "three";
import gsap from "gsap";
import { createPosterField, makePose } from "./posters";
import { createRig } from "./rig";
import { createPost } from "./post";
import { createCaptions } from "./captions";
import { hookFade, deferDisposal, type Fader } from "./fade";
import type { Mode, ModeContext, WorldData } from "./types";
import { UI } from "../ui/ui";

export async function createEngine(root: HTMLElement, world: WorldData, modes: Mode[], ui: UI) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance", stencil: false });
  renderer.setPixelRatio(Math.min(2, devicePixelRatio));
  renderer.setSize(innerWidth, innerHeight);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  root.prepend(renderer.domElement);
  renderer.domElement.className = "stage";

  let dprCap = Math.min(2, devicePixelRatio);
  const dprMax = dprCap;
  const scene = new THREE.Scene();
  const bgCur = new THREE.Color("#E7E5E0");
  scene.background = bgCur; // not setClearColor (TECH.md 0.1)
  const camera = new THREE.PerspectiveCamera(36, innerWidth / innerHeight, 0.3, 2000);
  camera.position.set(0, 0, 22);
  const R = createRig(camera);
  const rig = R.rig;
  const post = createPost(renderer, scene, camera);

  const posters = await createPosterField(world, scene, (f) => ui.setProgress(0.15 + f * 0.8));
  posters.setBackground(bgCur);
  const overlay = document.createElement("div");
  overlay.className = "mode-overlay";
  root.append(overlay);

  const capLayer = createCaptions(root, world, posters, scene, camera);
  const group = new THREE.Group();
  scene.add(group);
  const raycaster = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  const hit: THREE.Intersection[] = [];
  const focusVec = new THREE.Vector3();
  const tmp = new THREE.Vector3();
  const tmp2 = new THREE.Vector2();

  function rayAt(x: number, y: number) {
    ndc.set((x / innerWidth) * 2 - 1, -(y / innerHeight) * 2 + 1);
    raycaster.setFromCamera(ndc, camera);
    return raycaster.ray;
  }
  const ctx: ModeContext = {
    world, mine: world.nodes.slice(0, world.mineCount), renderer, scene, group, rig, posters, overlay,
    size: { width: innerWidth, height: innerHeight, dpr: renderer.getPixelRatio() },
    focused: null, post, controls: R.controls, background: bgCur,
    captions: capLayer,
    layout: {
      focusRect() {
        const w = innerWidth, h = innerHeight;
        if (w <= 760) { const hh = h * 0.48; return { x: 0, y: 0, width: w, height: hh, centerX: w / 2, centerY: hh / 2 }; }
        const ww = w * 0.62; return { x: 0, y: 0, width: ww, height: h, centerX: ww / 2, centerY: h / 2 };
      },
      focusNdc() { const r = this.focusRect(); return { x: (r.centerX / innerWidth) * 2 - 1, y: -((r.centerY / innerHeight) * 2 - 1) }; },
      worldAt(nx, ny, d, out = new THREE.Vector3()) {
        camera.updateMatrixWorld();
        const ray = out.set(nx, ny, 0.5).unproject(camera).sub(camera.position).normalize();
        const fwd = camera.getWorldDirection(tmp);
        const t = d / Math.max(0.05, ray.dot(fwd));
        return out.copy(ray).multiplyScalar(t).add(camera.position);
      },
      frame(target, distance, dir = new THREE.Vector3(0, 0, 1)) {
        const n = this.focusNdc();
        const pos = target.clone().addScaledVector(dir.clone().normalize(), distance);
        // shift the look-at sideways/up so the target lands at ndc (n.x, n.y)
        const half = Math.tan((camera.fov * Math.PI) / 360) * distance;
        const fwd = dir.clone().normalize().negate();
        const right = new THREE.Vector3().crossVectors(fwd, new THREE.Vector3(0, 1, 0)).normalize();
        const up = new THREE.Vector3().crossVectors(right, fwd).normalize();
        const look = target.clone().addScaledVector(right, -n.x * half * camera.aspect).addScaledVector(up, -n.y * half);
        pos.addScaledVector(right, -n.x * half * camera.aspect).addScaledVector(up, -n.y * half);
        return { position: pos, lookAt: look };
      },
    },
    open: (id) => open(id), close: () => close(),
    hover(id, x = 0, y = 0) {
      posters.setHover(id, ndc.x, ndc.y);
      if (id == null) { ui.hideTip(); return; }
      const nd = world.nodes[id];
      let ax = x + 16, ay = y;
      if (id < world.mineCount) { posters.hoverAnchor(id, camera, tmp2); ax = tmp2.x * innerWidth + 14; ay = tmp2.y * innerHeight; }
      ui.showTip(nd.title + (nd.year ? "  " + nd.year : ""), ax, ay);
    },
    pickPoster(x, y) {
      const ray = rayAt(x, y);
      const pid = posters.pick(ray);
      let pd = Infinity;
      if (pid != null) { posters.current(pid, tmp); pd = tmp.sub(ray.origin).dot(ray.direction); }
      const ghostMeshes = scene.children.filter((o) => o.userData.nodeId != null);
      hit.length = 0;
      raycaster.intersectObjects(ghostMeshes, false, hit);
      if (hit.length && hit[0].distance < pd) return hit[0].object.userData.nodeId as number;
      return pid;
    },
    reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
  };

  /* ---------- mode switching: one continuous world ---------- */
  let mode: Mode | null = null;
  let t0 = performance.now() / 1000;
  let switching = 0;
  const inFade: Fader = { v: 1 };
  let inTween: gsap.core.Tween | null = null;
  const target = new THREE.Color();

  // Switches run one at a time: a mode's async enter() must finish before its exit() runs, or it
  // would add objects after the cleanup. Requests made meanwhile collapse to the latest one.
  let wanted = "";
  let running: Promise<void> | null = null;
  function switchTo(id: string): Promise<void> {
    wanted = (modes.find((m) => m.id === id) || modes[0]).id;
    if (!running) {
      running = (async () => {
        try { while (mode?.id !== wanted) await doSwitch(wanted); }
        finally { running = null; }
      })();
    }
    return running;
  }

  async function doSwitch(id: string) {
    const next = modes.find((m) => m.id === id) || modes[0];
    const token = ++switching;
    const keep = ctx.focused;
    if (ctx.focused != null) close();
    const first = !mode;

    // old environment: fades out (0..0.9s), real disposal deferred until it is gone
    if (mode) {
      const real = deferDisposal(group);
      const fader: Fader = { v: 1 };
      hookFade(group, fader);
      const prevOverlay = [...overlay.childNodes];
      mode.exit(ctx);
      R.disposeOrbit();
      const og = new THREE.Group();
      scene.add(og);
      if (group.children.length) og.add(...group.children); // modes may have no objects of their own now
      const od = document.createElement("div");
      od.className = "mode-overlay-out";
      od.append(...prevOverlay);
      root.append(od);
      gsap.to(od, { opacity: 0, duration: 0.7, ease: "power2.in", onComplete: () => od.remove() });
      gsap.to(fader, { v: 0, duration: 0.9, ease: "power2.in", onComplete: () => { scene.remove(og); real(); } });
    }
    group.clear(); overlay.replaceChildren(); posters.clearGhosts(); posters.dim(0); capLayer.clear();
    post.focusOn(null, { seconds: 0.2 });
    rig.parallax = 0; rig.sway = 0; rig.damping = 0.12;
    posters.rimStrength = 1; posters.uniforms.uSwirl.value = 1; posters.uniforms.uOpacityMul.value = 1;
    scene.fog = null;

    mode = next;
    ui.setTheme(next.theme); ui.setActive(next.id);
    target.set(next.background);
    gsap.to(bgCur, { duration: first ? 0.01 : 1.2, delay: first ? 0 : 0.3, ease: "sine.inOut", r: target.r, g: target.g, b: target.b, overwrite: true });
    // every effect is off by default; a mode opts in from enter() (post.set kills this tween)
    post.set({ grain: 0, vignette: 0, bloom: 0, dof: 0, aberration: 0 }, 0.01);
    post.bloomThreshold = 1.0;
    if (location.hash !== "#" + next.id) history.replaceState(null, "", "#" + next.id);
    t0 = performance.now() / 1000;

    // new environment: fades in late (1.0s .. 2.2s)
    inTween?.kill();
    inFade.v = first ? 1 : 0;
    const entering = next.enter(ctx);
    if (!first) {
      hookFade(group, inFade);
      setTimeout(() => hookFade(group, inFade), 250);
      setTimeout(() => hookFade(group, inFade), 900);
      inTween = gsap.to(inFade, { v: 1, duration: 1.2, delay: 1.0, ease: "power2.out" });
    }
    try { await entering; } catch (e) { console.error(e); }
    if (token !== switching) return;
    if (keep != null) setTimeout(() => { if (token === switching && mode === next && ctx.focused == null) open(keep); }, 200);
  }

  function open(id: number) {
    if (!world.nodes[id]) return;
    ctx.focused = id;
    document.body.classList.add("focused");
    ctx.hover(null);
    posters.sharp([id, ...world.nodes[id].next.filter((x) => x < world.mineCount)]);
    mode?.focus(ctx, id);
    post.focusOn(focusVec);
  }
  function close() {
    if (ctx.focused == null) return;
    ctx.focused = null;
    document.body.classList.remove("focused");
    capLayer.clear();
    posters.sharp([]);
    mode?.unfocus(ctx);
    post.focusOn(null);
  }

  /* ---------- input ---------- */
  const el = renderer.domElement;
  let down: { x: number; y: number; moved: boolean } | null = null;
  const touches = new Map<number, { x: number; y: number }>();
  let pinch = 0;
  let pendingMove: PointerEvent | null = null;
  el.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "touch") { touches.set(e.pointerId, { x: e.clientX, y: e.clientY }); if (touches.size === 2) { const [a, b] = [...touches.values()]; pinch = Math.hypot(a.x - b.x, a.y - b.y); } }
    down = { x: e.clientX, y: e.clientY, moved: false };
    el.setPointerCapture(e.pointerId);
    const used = mode?.pointer?.(ctx, e, "down");
    if (!used) R.orbit?.pointer(e, "down");
  });
  el.addEventListener("pointermove", (e) => {
    R.setPointer((e.clientX / innerWidth) * 2 - 1, -((e.clientY / innerHeight) * 2 - 1));
    if (touches.has(e.pointerId)) {
      touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (touches.size === 2) {
        const [a, b] = [...touches.values()]; const d = Math.hypot(a.x - b.x, a.y - b.y);
        const we = { deltaY: (pinch - d) * 2, preventDefault() {} } as unknown as WheelEvent;
        if (!mode?.wheel?.(ctx, we)) R.orbit?.wheel(we);
        pinch = d; if (down) down.moved = true; return;
      }
    }
    if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > 4) down.moved = true;
    const used = mode?.pointer?.(ctx, e, "move");
    if (down && !used) R.orbit?.pointer(e, "move");
    if (!down && !used && e.pointerType !== "touch") pendingMove = e;
  });
  const up = (e: PointerEvent) => {
    touches.delete(e.pointerId);
    const used = mode?.pointer?.(ctx, e, "up");
    R.orbit?.pointer(e, "up");
    if (down && !down.moved && !used && touches.size === 0) { const id = ctx.pickPoster(e.clientX, e.clientY); id != null ? open(id) : close(); }
    down = null;
  };
  el.addEventListener("pointerup", up);
  el.addEventListener("pointercancel", up);
  el.addEventListener("pointerleave", () => { pendingMove = null; ctx.hover(null); });
  el.addEventListener("wheel", (e) => { e.preventDefault(); if (!mode?.wheel?.(ctx, e)) R.orbit?.wheel(e); }, { passive: false });
  addEventListener("keydown", (e) => {
    if (mode?.key?.(ctx, e)) return;
    if (e.key === "Escape" || e.key === "Backspace") close();
    const k = Number(e.key);
    if (k >= 1 && k <= modes.length && !e.metaKey && !e.ctrlKey && !e.altKey) switchTo(modes[k - 1].id);
  });
  function resize() {
    const w = innerWidth, h = innerHeight;
    renderer.setPixelRatio(dprCap);
    post.composer.setSize(w, h);
    camera.aspect = w / h; camera.updateProjectionMatrix();
    ctx.size = { width: w, height: h, dpr: renderer.getPixelRatio() };
    const db = renderer.getDrawingBufferSize(new THREE.Vector2());
    scene.traverse((o: any) => { for (const m of ([] as any[]).concat(o.material ?? [])) if (m.uniforms?.uRes) m.uniforms.uRes.value.copy(db); });
    mode?.resize?.(ctx);
  }
  addEventListener("resize", resize);
  resize();
  addEventListener("hashchange", () => switchTo(location.hash.slice(1)));

  /* ---------- adaptive resolution: stay inside the frame budget ---------- */
  let ema = 1 / 60, since = 0, floor = 1 / 60, floorAge = 0;
  post.composer.multisampling = dprCap >= 1.75 ? 0 : 4;
  function adapt(dt: number) {
    ema += (dt - ema) * 0.05; since += dt; floorAge += dt;
    if (dt < floor) floor = dt; // the display's refresh interval
    if (floorAge > 12) { floor = ema; floorAge = 0; }
    if (since < 1.2) return;
    since = 0;
    if (ema > floor * 1.3 && dprCap > 1) { dprCap = Math.max(1, dprCap - 0.25); applyDpr(); }
    else if (ema < floor * 1.07 && dprCap < dprMax) { dprCap = Math.min(dprMax, dprCap + 0.25); applyDpr(); }
  }
  function applyDpr() {
    renderer.setPixelRatio(dprCap);
    post.composer.multisampling = dprCap >= 1.75 ? 0 : 4;
    post.composer.setSize(innerWidth, innerHeight);
    ctx.size = { width: innerWidth, height: innerHeight, dpr: dprCap };
    const db = renderer.getDrawingBufferSize(new THREE.Vector2());
    scene.traverse((o: any) => { for (const m of ([] as any[]).concat(o.material ?? [])) if (m.uniforms?.uRes) m.uniforms.uRes.value.copy(db); });
  }

  /* ---------- loop ---------- */
  let last = performance.now() / 1000;
  const start = last;
  let hoverCool = 0;
  renderer.setAnimationLoop(() => {
    const now = performance.now() / 1000;
    const dt = Math.min(0.05, Math.max(0.0001, now - last)); last = now;
    if (pendingMove && !down) {
      hoverCool -= dt;
      if (hoverCool <= 0) {
        hoverCool = 0.016;
        const e = pendingMove; pendingMove = null;
        const id = ctx.pickPoster(e.clientX, e.clientY);
        ctx.hover(id, e.clientX, e.clientY);
      }
    }
    mode?.update(ctx, dt, now - t0);
    R.orbit?.update(dt);
    R.update(dt, now - start, ctx.size);
    if (ctx.focused != null) {
      const id = ctx.focused;
      if (id < world.mineCount) posters.current(id, focusVec);
      else { const g = scene.children.find((o) => o.userData.nodeId === id); if (g) focusVec.copy(g.position); }
    }
    ui.tick(dt);
    adapt(dt);
    posters.setBackground(bgCur); // dim / ghosts fade toward the live ground colour
    posters.tick(now, dt, camera, { w: ctx.size.width, h: ctx.size.height });
    capLayer.update(dt);
    if (post.active()) post.composer.render(dt); else renderer.render(scene, camera);
  });

  /* ---------- intro: his posters stack into a spine, then the first mode takes over ---------- */
  function spine(fast: boolean) {
    const n = world.mineCount;
    const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => (world.nodes[a].first ?? "").localeCompare(world.nodes[b].first ?? ""));
    const pose = makePose(n);
    const sc = 0.4, gap = 0.0128;
    const q = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -Math.PI / 2);
    const seq = new Float32Array(n);
    order.forEach((id, k) => {
      pose.position.set([0, (k - n / 2) * gap, 0], id * 3);
      pose.quaternion.set([q.x, q.y, q.z, q.w], id * 4);
      pose.scale[id] = sc; pose.opacity[id] = 1;
      seq[id] = k / n;
    });
    rig.position.set(0, 0, 21); rig.lookAt.set(0, 0, 0);
    return posters.morphTo(pose, { duration: fast ? 0.5 : 0.9, stagger: fast ? 0.3 : 1.2, ease: "outExpo", order: seq });
  }
  async function intro(full: boolean, firstMode: string) {
    // window.spine appears when the world has settled (captures wait for it); fallback after 14 s
    const expose = () => { (window as any).spine = api; };
    setTimeout(expose, 14000);
    if (full) {
      ui.finishIntro(true);
      spine(false);
      await new Promise((r) => setTimeout(r, 1500));
      await switchTo(firstMode);
    } else {
      ui.finishIntro(false);
      await switchTo(firstMode);
    }
    expose();
  }
  const api = { switchTo, open, close, ctx, renderer, scene, camera, post, rig, intro };
  return api;
}
