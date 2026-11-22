/* Environment crossfade + deferred disposal, so mode switches never pop. Works on any
   Object3D tree: material.opacity, shader uniforms uOpacity/uAlpha/uFade.
   Opacity is multiplied in onBeforeRender and restored in onAfterRender, so modes that animate
   their own opacity every frame keep working. */
import * as THREE from "three";

export interface Fader { v: number }
const hooked = new WeakSet<THREE.Object3D>();
const UNI = ["uOpacity", "uAlpha", "uFade"];

export function hookFade(root: THREE.Object3D, f: Fader) {
  root.traverse((o: any) => {
    if (hooked.has(o)) return;
    const mats: any[] = ([] as any[]).concat(o.material ?? []);
    if (!mats.length) return;
    hooked.add(o);
    for (const m of mats) m.transparent = true;
    const ob = o.onBeforeRender, oa = o.onAfterRender;
    let saved: number[] = [], savedU: [any, number][] = [];
    o.onBeforeRender = function (...a: any[]) {
      saved = mats.map((m) => m.opacity); savedU = [];
      for (const m of mats) {
        m.opacity *= f.v;
        if (m.uniforms) for (const k of UNI) { const u = m.uniforms[k]; if (u && typeof u.value === "number") { savedU.push([u, u.value]); u.value *= f.v; } }
      }
      ob?.apply(this, a);
    };
    o.onAfterRender = function (...a: any[]) {
      oa?.apply(this, a);
      mats.forEach((m, i) => (m.opacity = saved[i]));
      for (const [u, v] of savedU) u.value = v;
    };
  });
}

/** Replace dispose() on everything under root with a recorder; returns a function that really disposes. */
export function deferDisposal(root: THREE.Object3D) {
  const real: (() => void)[] = [];
  const seen = new Set<any>();
  const defer = (x: any) => {
    if (!x || seen.has(x) || typeof x.dispose !== "function") return;
    seen.add(x);
    const d = x.dispose.bind(x);
    real.push(d);
    x.dispose = () => {};
  };
  root.traverse((o: any) => {
    defer(o); defer(o.geometry);
    for (const m of ([] as any[]).concat(o.material ?? [])) {
      defer(m);
      for (const k in m) if (m[k] && m[k].isTexture) defer(m[k]);
      if (m.uniforms) for (const k in m.uniforms) { const v = m.uniforms[k]?.value; if (v && v.isTexture) defer(v); }
    }
  });
  return () => real.forEach((d) => { try { d(); } catch { /* ignore */ } });
}
