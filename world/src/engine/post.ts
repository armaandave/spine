/* Post-processing (pmndrs): render → DoF (only when a mode asks) → bloom → aberration → vignette → grain.
   Every effect defaults to 0 and the engine skips the composer entirely (`active()` false → direct render).
   Modes opt in via ctx.post.set(). Values ease. */
import * as THREE from "three";
import {
  BlendFunction, BloomEffect, ChromaticAberrationEffect, DepthOfFieldEffect, EffectComposer, EffectPass,
  NoiseEffect, RenderPass, VignetteEffect,
} from "postprocessing";
import gsap from "gsap";
import type { PostFX, PostSettings } from "./types";

export function createPost(renderer: THREE.WebGLRenderer, scene: THREE.Scene, camera: THREE.PerspectiveCamera) {
  const composer = new EffectComposer(renderer, { multisampling: 4, frameBufferType: THREE.HalfFloatType });
  composer.addPass(new RenderPass(scene, camera));
  const dof = new DepthOfFieldEffect(camera, { focusDistance: 0.02, focusRange: 0.01, bokehScale: 0, resolutionScale: 0.5 });
  const bloom = new BloomEffect({ intensity: 0, luminanceThreshold: 1.0, luminanceSmoothing: 0.025, mipmapBlur: true, radius: 0.55 });
  const aberr = new ChromaticAberrationEffect({ offset: new THREE.Vector2(0, 0), radialModulation: true, modulationOffset: 0.3 });
  const vig = new VignetteEffect({ offset: 0.3, darkness: 0 });
  const noise = new NoiseEffect({ blendFunction: BlendFunction.SOFT_LIGHT, premultiply: false });
  const dofPass = new EffectPass(camera, dof);
  composer.addPass(dofPass);
  composer.addPass(new EffectPass(camera, bloom, aberr, vig, noise));

  const s: PostSettings = { grain: 0, vignette: 0, bloom: 0, dof: 0, aberration: 0 };
  let tween: gsap.core.Tween | null = null;
  const apply = () => {
    noise.blendMode.opacity.value = Math.min(1, s.grain * 3.5);
    vig.darkness = s.vignette;
    bloom.intensity = s.bloom;
    dof.bokehScale = s.dof;
    dofPass.enabled = s.dof > 0.03;
    aberr.offset.set(s.aberration, s.aberration);
  };
  apply();
  const api: PostFX & { composer: EffectComposer; active(): boolean; update(dt: number): void; setSize(w: number, h: number): void } = {
    composer, settings: s,
    /** true when any effect is on: otherwise the engine renders straight to the screen */
    active() { return s.grain > 0.002 || s.vignette > 0.002 || s.bloom > 0.002 || s.aberration > 0.00003 || s.dof > 0.03; },
    set(v, seconds = 0.8) {
      tween?.kill();
      const from = { ...s };
      const to = { ...s, ...v };
      const st = { t: 0 };
      tween = gsap.to(st, {
        t: 1, duration: Math.max(0.001, seconds), ease: "sine.inOut",
        onUpdate: () => { for (const k of Object.keys(s) as (keyof PostSettings)[]) s[k] = from[k] + (to[k] - from[k]) * st.t; apply(); },
      });
    },
    /** Depth of field aims at this point, but only shows when a mode set dof > 0 itself. */
    focusOn(p, opts = {}) {
      dof.target = p ?? null;
      dof.cocMaterial.focusRange = opts.range ?? 6;
    },
    get bloomThreshold() { return bloom.luminanceMaterial.threshold; },
    set bloomThreshold(v: number) { bloom.luminanceMaterial.threshold = v; },
    update(dt) { void dt; },
    setSize(w, h) { composer.setSize(w, h, false); },
  };
  return api;
}
