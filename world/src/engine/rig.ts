/* Camera rig: critically damped spring toward the mode's desired pose, gsap flyTo with a
   bowed path, pointer parallax + idle sway, and a reusable damped orbit controller. */
import * as THREE from "three";
import gsap from "gsap";
import type { CameraRig, Controls, OrbitControls, OrbitOptions } from "./types";

function smoothDamp(cur: number, target: number, vel: { v: number }, smoothTime: number, dt: number) {
  const omega = 2 / Math.max(0.0001, smoothTime);
  const x = omega * dt;
  const exp = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x);
  const change = cur - target;
  const temp = (vel.v + omega * change) * dt;
  vel.v = (vel.v - omega * temp) * exp;
  return target + (change + temp) * exp;
}
const V = () => ({ v: 0 });

export function createRig(camera: THREE.PerspectiveCamera) {
  const desiredPos = new THREE.Vector3(0, 0, 10), desiredLook = new THREE.Vector3();
  const pos = desiredPos.clone(), look = desiredLook.clone();
  const vp = [V(), V(), V()], vl = [V(), V(), V()];
  const pointer = new THREE.Vector2(), pointerS = new THREE.Vector2(), pointerV = [V(), V()];
  let flight: gsap.core.Tween | null = null;
  let parallaxHold = 0; // seconds since the last flyTo ended → ramps the parallax back
  let flying = false;
  const rig: CameraRig = {
    camera, position: desiredPos, lookAt: desiredLook, damping: 0.12, parallax: 0, sway: 0,
    get flying() { return flying; },
    stop() { flight?.kill(); flight = null; flying = false; },
    flyTo(position, lookAt, opts = {}) {
      flight?.kill();
      const p0 = desiredPos.clone(), l0 = desiredLook.clone();
      const p1 = position.clone(), l1 = lookAt.clone();
      const st = { t: 0, lt: 0 };
      const fov0 = camera.fov, fov1 = opts.fov ?? camera.fov;
      const chord = p1.clone().sub(p0);
      const dist = chord.length();
      const arc = opts.arc ?? 0.12;
      // bow sideways, perpendicular to the chord and to world up (falls back to camera-right)
      const side = new THREE.Vector3().crossVectors(chord, new THREE.Vector3(0, 1, 0));
      if (side.lengthSq() < 1e-6) side.set(1, 0, 0); else side.normalize();
      const lift = new THREE.Vector3(0, 1, 0).multiplyScalar(0.35);
      flying = true; parallaxHold = 0;
      return new Promise((resolve) => {
        flight = gsap.to(st, {
          t: 1, lt: 1, duration: opts.duration ?? 1.4, ease: opts.ease ?? "power3.inOut",
          onUpdate: () => {
            const k = st.t;
            const bow = Math.sin(Math.PI * k) * arc * dist;
            desiredPos.lerpVectors(p0, p1, k).addScaledVector(side, bow).addScaledVector(lift, bow);
            // look leads the position slightly
            const kl = Math.min(1, k * 1.08);
            desiredLook.lerpVectors(l0, l1, kl);
            if (fov1 !== fov0) { camera.fov = fov0 + (fov1 - fov0) * k; camera.updateProjectionMatrix(); }
          },
          onComplete: () => { flying = false; flight = null; resolve(); },
          onInterrupt: () => { resolve(); },
        });
      });
    },
  };
  const right = new THREE.Vector3(), up = new THREE.Vector3(), fwd = new THREE.Vector3();
  const lookNow = new THREE.Vector3();

  function update(dt: number, time: number, size: { width: number; height: number }) {
    const tau = Math.max(0.03, (1 / 60) / -Math.log(1 - Math.min(0.99, Math.max(0.005, rig.damping))));
    const direct = active?.dragging ?? false;
    const stP = direct ? 0.012 : tau * 0.9, stL = direct ? 0.012 : stP / 1.22;
    pos.set(smoothDamp(pos.x, desiredPos.x, vp[0], stP, dt), smoothDamp(pos.y, desiredPos.y, vp[1], stP, dt), smoothDamp(pos.z, desiredPos.z, vp[2], stP, dt));
    look.set(smoothDamp(look.x, desiredLook.x, vl[0], stL, dt), smoothDamp(look.y, desiredLook.y, vl[1], stL, dt), smoothDamp(look.z, desiredLook.z, vl[2], stL, dt));
    pointerS.set(smoothDamp(pointerS.x, pointer.x, pointerV[0], 0.45, dt), smoothDamp(pointerS.y, pointer.y, pointerV[1], 0.45, dt));
    if (!flying) parallaxHold += dt;
    const pk = flying ? 0 : Math.min(1, parallaxHold / 1.0);
    const d = pos.distanceTo(look);
    camera.position.copy(pos);
    camera.lookAt(look);
    camera.updateMatrixWorld();
    right.setFromMatrixColumn(camera.matrixWorld, 0); up.setFromMatrixColumn(camera.matrixWorld, 1);
    const A = d * 0.022 * rig.parallax * pk;
    const S = d * 0.0045 * rig.sway;
    camera.position
      .addScaledVector(right, pointerS.x * A + Math.sin(time * 0.31) * S + Math.sin(time * 0.17 + 1.3) * S * 0.6)
      .addScaledVector(up, pointerS.y * A * 0.7 + Math.cos(time * 0.23) * S * 0.7);
    lookNow.copy(look);
    camera.lookAt(lookNow);
    fwd.copy(look);
    void size; void fwd;
  }
  function setPointer(nx: number, ny: number) { pointer.set(nx, ny); }

  /* ------------------------------------------------------------ orbit controls */
  let active: OrbitImpl | null = null;
  class OrbitImpl implements OrbitControls {
    enabled = true;
    target = new THREE.Vector3();
    azimuth = 0; polar = Math.PI / 2.4; distance = 20;
    autoRotate = 0; minDistance = 1; maxDistance = 400;
    minPolar = 0.05; maxPolar = Math.PI - 0.05;
    dragging = false;
    private rotateSpeed = 1; private zoomSpeed = 1; private inertia = 0.92;
    private vAz = 0; private vPo = 0;
    private cur = { az: 0, po: 0, r: 20, tx: 0, ty: 0, tz: 0 };
    private last = { x: 0, y: 0, t: 0 };
    constructor(o: OrbitOptions) {
      if (o.target) this.target.copy(o.target);
      this.azimuth = o.azimuth ?? 0; this.polar = o.polar ?? Math.PI / 2.4; this.distance = o.distance ?? 20;
      this.minDistance = o.minDistance ?? 1; this.maxDistance = o.maxDistance ?? 400;
      this.minPolar = o.minPolar ?? 0.05; this.maxPolar = o.maxPolar ?? Math.PI - 0.05;
      this.rotateSpeed = o.rotateSpeed ?? 1; this.zoomSpeed = o.zoomSpeed ?? 1; this.inertia = o.inertia ?? 0.92;
      this.autoRotate = o.autoRotate ?? 0;
      this.cur = { az: this.azimuth, po: this.polar, r: this.distance, tx: this.target.x, ty: this.target.y, tz: this.target.z };
      this.apply(true);
    }
    syncFromRig() {
      const o = desiredPos.clone().sub(desiredLook);
      const r = o.length() || 1;
      this.distance = this.cur.r = r;
      this.polar = this.cur.po = Math.acos(THREE.MathUtils.clamp(o.y / r, -1, 1));
      this.azimuth = this.cur.az = Math.atan2(o.x, o.z);
      this.target.copy(desiredLook);
      this.cur.tx = desiredLook.x; this.cur.ty = desiredLook.y; this.cur.tz = desiredLook.z;
      this.vAz = this.vPo = 0;
    }
    nudge(da: number, dp: number) { this.vAz += da; this.vPo += dp; }
    pointer(e: PointerEvent, phase: "down" | "move" | "up") {
      if (!this.enabled) return false;
      if (phase === "down") { this.dragging = true; this.last = { x: e.clientX, y: e.clientY, t: performance.now() }; this.vAz = this.vPo = 0; return false; }
      if (phase === "move" && this.dragging) {
        const dx = e.clientX - this.last.x, dy = e.clientY - this.last.y;
        const now = performance.now(), dts = Math.max(0.004, (now - this.last.t) / 1000);
        const k = (Math.PI * 2 / Math.max(500, innerHeight)) * this.rotateSpeed * 0.9;
        this.azimuth -= dx * k; this.polar = THREE.MathUtils.clamp(this.polar - dy * k, this.minPolar, this.maxPolar);
        const f = 0.4;
        this.vAz = this.vAz * (1 - f) + (-dx * k / dts) * f; this.vPo = this.vPo * (1 - f) + (-dy * k / dts) * f;
        this.last = { x: e.clientX, y: e.clientY, t: now };
        return Math.abs(dx) + Math.abs(dy) > 0;
      }
      if (phase === "up") {
        this.dragging = false;
        if (performance.now() - this.last.t > 90) this.vAz = this.vPo = 0; // held still before release: no flick
      }
      return false;
    }
    wheel(e: WheelEvent) {
      if (!this.enabled) return false;
      const k = Math.exp(THREE.MathUtils.clamp(e.deltaY, -160, 160) * 0.0012 * this.zoomSpeed);
      this.distance = THREE.MathUtils.clamp(this.distance * k, this.minDistance, this.maxDistance);
      return true;
    }
    private apply(instant = false) {
      const c = this.cur;
      const sph = new THREE.Spherical(c.r, c.po, c.az);
      const o = new THREE.Vector3().setFromSpherical(sph);
      desiredLook.set(c.tx, c.ty, c.tz);
      desiredPos.copy(desiredLook).add(o);
      if (instant) { pos.copy(desiredPos); look.copy(desiredLook); }
    }
    update(dt: number) {
      if (!this.enabled) return;
      if (!this.dragging) {
        this.azimuth += (this.vAz + this.autoRotate) * dt;
        this.polar = THREE.MathUtils.clamp(this.polar + this.vPo * dt, this.minPolar, this.maxPolar);
        const decay = Math.exp(dt * Math.log(Math.max(0.01, this.inertia)) * 60);
        this.vAz *= decay; this.vPo *= decay;
      }
      this.distance = THREE.MathUtils.clamp(this.distance, this.minDistance, this.maxDistance);
      const k = this.dragging ? 1 : 1 - Math.exp(-dt * 9);
      const c = this.cur;
      c.az += (this.azimuth - c.az) * k; c.po += (this.polar - c.po) * k;
      c.r += (this.distance - c.r) * (1 - Math.exp(-dt * 8));
      const kt = 1 - Math.exp(-dt * 6);
      c.tx += (this.target.x - c.tx) * kt; c.ty += (this.target.y - c.ty) * kt; c.tz += (this.target.z - c.tz) * kt;
      this.apply();
    }
    dispose() { this.enabled = false; if (active === this) active = null; }
  }
  const controls: Controls = {
    orbit(opts = {}) { active?.dispose(); active = new OrbitImpl(opts); return active; },
  };
  return {
    rig, controls, update, setPointer,
    get orbit() { return active; },
    disposeOrbit() { active?.dispose(); },
  };
}
