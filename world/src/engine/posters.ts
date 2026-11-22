/* PosterField v1: his 990 posters as ONE instanced mesh whose morph runs on the GPU.
   Every instance carries from/to poses + a start time + duration; the vertex shader
   evaluates a cubic-bezier arc (outward drift + a global swirl) with a per-poster ease,
   so a mode switch is one uniform (uNow) ticking. The CPU mirrors the same maths
   (evalPose) for picking, current(i), hi-res selection and for resuming from a
   mid-flight state. Opaque + depth-write (no sorting artifacts); partial opacity is
   dithered; dim fades towards the ground colour. Real box thickness, edge light.
   The API in types.ts stays. */
import * as THREE from "three";
import type { Ghost, MorphOptions, PoseBuffer, PosterField, WorldData } from "./types";

const EASE_ID: Record<string, number> = { outExpo: 0, inOutExpo: 1, outQuart: 2, inOutCubic: 3, outCubic: 4 };
const EASE: ((t: number) => number)[] = [
  (t) => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t)),
  (t) => (t <= 0 ? 0 : t >= 1 ? 1 : t < 0.5 ? Math.pow(2, 20 * t - 10) / 2 : (2 - Math.pow(2, -20 * t + 10)) / 2),
  (t) => 1 - Math.pow(1 - t, 4),
  (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  (t) => 1 - Math.pow(1 - t, 3),
];

export function makePose(count: number): PoseBuffer {
  const p: PoseBuffer = {
    count,
    position: new Float32Array(count * 3),
    quaternion: new Float32Array(count * 4),
    scale: new Float32Array(count).fill(1),
    opacity: new Float32Array(count).fill(1),
    billboard: new Float32Array(count),
  };
  for (let i = 0; i < count; i++) p.quaternion[i * 4 + 3] = 1;
  return p;
}
function copyPose(dst: PoseBuffer, src: PoseBuffer) {
  dst.position.set(src.position); dst.quaternion.set(src.quaternion); dst.scale.set(src.scale);
  dst.opacity.set(src.opacity); dst.billboard.set(src.billboard);
}

const imgCache = new Map<string, Promise<HTMLImageElement>>();
export function loadImage(url: string): Promise<HTMLImageElement> {
  let p = imgCache.get(url);
  if (!p) {
    p = new Promise((res, rej) => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => res(img); img.onerror = rej;
      img.src = url;
    });
    imgCache.set(url, p);
  }
  return p;
}

/** Base-atlas sheets holding his titles, packed into one sRGB array texture. */
async function buildArrayTexture(world: WorldData, sheetIds: number[], progress: (f: number) => void) {
  const { atlas } = world;
  const first = await loadImage(atlas.sheets[sheetIds[0]]);
  const w = first.naturalWidth, h = first.naturalHeight;
  const data = new Uint8Array(w * h * 4 * sheetIds.length);
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const c2 = canvas.getContext("2d", { willReadFrequently: true })!;
  for (let l = 0; l < sheetIds.length; l++) {
    const img = l === 0 ? first : await loadImage(atlas.sheets[sheetIds[l]]);
    c2.clearRect(0, 0, w, h);
    c2.drawImage(img, 0, 0);
    data.set(c2.getImageData(0, 0, w, h).data, l * w * h * 4);
    progress((l + 1) / sheetIds.length);
  }
  const tex = new THREE.DataArrayTexture(data, w, h, sheetIds.length);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.generateMipmaps = true;
  tex.anisotropy = 16;
  tex.needsUpdate = true;
  return { tex, w, h };
}

/* ------------------------------------------------------------------ shaders */

const COMMON = /* glsl */ `
  uniform float uNow, uThick, uFrame, uRimStrength, uBreath;
  uniform vec3 uCamPos, uLightDir, uBg;
  uniform vec4 uCamQ;
  uniform vec2 uTilt;
  vec3 qrot(vec4 q, vec3 v) { return v + 2.0 * cross(q.xyz, cross(q.xyz, v) + q.w * v); }
  vec4 qnlerp(vec4 a, vec4 b, float t) { if (dot(a, b) < 0.0) b = -b; return normalize(mix(a, b, t)); }
  vec4 qaxis(vec3 ax, float ang) { return vec4(ax * sin(ang * 0.5), cos(ang * 0.5)); }
  vec4 qmul(vec4 a, vec4 b) { return vec4(a.w * b.xyz + b.w * a.xyz + cross(a.xyz, b.xyz), a.w * b.w - dot(a.xyz, b.xyz)); }
`;

const VERT = /* glsl */ `
  ${COMMON}
  uniform float uSwirl;
  attribute vec4 aFrom, aFromQ, aTo, aToQ, aMisc, aTime, aCell, aState, aCol;
  attribute vec2 aHero;
  flat varying vec2 vHero;
  varying vec2 vUv; varying vec3 vN; varying vec3 vWP; varying float vAlpha;
  flat varying vec4 vCell; flat varying vec4 vState; flat varying vec3 vCol; flat varying float vFace;
  float ease(float id, float t) {
    if (id < 0.5) return t >= 1.0 ? 1.0 : 1.0 - pow(2.0, -10.0 * t);
    if (id < 1.5) return t <= 0.0 ? 0.0 : t >= 1.0 ? 1.0 : t < 0.5 ? pow(2.0, 20.0 * t - 10.0) * 0.5 : (2.0 - pow(2.0, -20.0 * t + 10.0)) * 0.5;
    if (id < 2.5) return 1.0 - pow(1.0 - t, 4.0);
    if (id < 3.5) return t < 0.5 ? 4.0 * t * t * t : 1.0 - pow(-2.0 * t + 2.0, 3.0) * 0.5;
    return 1.0 - pow(1.0 - t, 3.0);
  }
  vec3 bez(vec3 a, vec3 b, vec3 c, vec3 d, float t) {
    float u = 1.0 - t;
    return u*u*u*a + 3.0*u*u*t*b + 3.0*u*t*t*c + t*t*t*d;
  }
  vec3 bezD(vec3 a, vec3 b, vec3 c, vec3 d, float t) {
    float u = 1.0 - t;
    return 3.0*u*u*(b-a) + 6.0*u*t*(c-b) + 3.0*t*t*(d-c);
  }
  void main() {
    float t = clamp((uNow - aTime.x) / aTime.y, 0.0, 1.0);
    float e = ease(aTime.z, t);
    vec3 A = aFrom.xyz, B = aTo.xyz, AB = B - A;
    float L = length(AB);
    vec3 P = B, tang = vec3(0.0, 0.0, 1.0);
    if (L > 1e-4 && t < 1.0) {
      vec3 up = vec3(0.0, 1.0, 0.0);
      vec3 sw = cross(up, AB); float sl = length(sw);
      sw = sl > 1e-5 ? sw / sl : vec3(1.0, 0.0, 0.0);
      sw *= uSwirl;
      vec3 oa = length(A) > 1e-3 ? normalize(A) : up;
      vec3 ob = length(B) > 1e-3 ? normalize(B) : up;
      vec3 c1 = A + oa * (0.35 * L) + sw * (0.25 * L);
      vec3 c2 = B - ob * (0.35 * L) + sw * (0.25 * L);
      P = bez(A, c1, c2, B, e);
      tang = normalize(bezD(A, c1, c2, B, e) + 1e-5);
    }
    float scl = mix(aFrom.w, aTo.w, e);
    float op = mix(aMisc.x, aMisc.y, e);
    vec4 qf = aMisc.z > 0.5 ? uCamQ : aFromQ;
    vec4 qt = aMisc.w > 0.5 ? uCamQ : aToQ;
    vec4 q = qnlerp(qf, qt, e);
    float asp = aCell.w > 0.5 ? 1.0 : 0.6666667;
    vec3 lp = vec3(position.x * asp, position.y, position.z * uThick);
    vec3 wp = P + qrot(q, lp * scl);
    vec3 toCam = normalize(uCamPos - P);
    wp += toCam * (float(gl_InstanceID % 61) * (0.0016 * scl)); // depth bias only (no z-fighting)
    vHero = aHero;
    vN = normalize(qrot(q, normal));
    vWP = wp; vUv = uv; vAlpha = op; vCell = aCell; vState = aState; vCol = aCol.rgb; vFace = normal.z;
    gl_Position = projectionMatrix * viewMatrix * vec4(wp, 1.0);
    if (op < 0.01) gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
  }`;

const FRAG_COMMON = /* glsl */ `
  uniform vec3 uFogColor; uniform vec3 uFogP; // near, far, density(exp2)
  uniform float uFogMode;
  uniform float uOpacityMul;
  float ign(vec2 p) { return fract(52.9829189 * fract(dot(p, vec2(0.06711056, 0.00583715)))); }
  vec3 shadePoster(vec3 art, vec3 dom, vec3 n, vec3 vwp, vec2 uv, float face, float dim, float hover) {
    vec3 V = normalize(uCamPos - vwp);
    vec3 N = normalize(n);
    float ndv = abs(dot(N, V));
    vec3 L = normalize(uLightDir);
    float lam = max(dot(N, L), 0.0);
    vec3 col;
    float dark = 1.0 - smoothstep(0.35, 0.75, dot(uBg, vec3(0.299, 0.587, 0.114)) * 4.0 + 0.0); // 1 on a dark ground
    if (face > 0.5) {
      // real artwork colour; shading is a whisper, the edge a thin hint
      col = art; // flat: the artwork exactly, no light, no edge
    } else if (face < -0.5) {
      col = art * 0.4 + dom * 0.06;
    } else {
      col = dom * (0.26 + 0.14 * lam); // thickness: a darker shade of the poster's own colour
    }
    col = mix(col, uBg, dim);
    float dist = length(uCamPos - vwp);
    float f = 0.0;
    if (uFogMode > 1.5) f = 1.0 - exp(-uFogP.z * uFogP.z * dist * dist);
    else if (uFogMode > 0.5) f = smoothstep(uFogP.x, uFogP.y, dist);
    col = mix(col, uFogColor, clamp(f, 0.0, 1.0));
    return col;
  }
  // no dither: low opacity is an opaque smooth fade to the ground colour; only a fully faded poster is dropped
  void fadeDiscard(float a) { if (a < 0.02) discard; }
`;

const FRAG = /* glsl */ `
  precision highp float; precision highp sampler2DArray;
  ${COMMON}
  ${FRAG_COMMON}
  uniform sampler2DArray uAtlas; uniform vec2 uCell; uniform float uCellAspect;
  uniform sampler2D uHi0, uHi1, uHi2, uHi3, uHi4, uHi5, uHi6, uHi7;
  uniform sampler2D uHero0, uHero1, uHero2, uHero3;
  flat varying vec2 vHero;
  varying vec2 vUv; varying vec3 vN; varying vec3 vWP; varying float vAlpha;
  flat varying vec4 vCell; flat varying vec4 vState; flat varying vec3 vCol; flat varying float vFace;
  vec4 hiSample(int s, vec2 st) {
    if (s == 0) return texture(uHi0, st); if (s == 1) return texture(uHi1, st); if (s == 2) return texture(uHi2, st);
    if (s == 3) return texture(uHi3, st); if (s == 4) return texture(uHi4, st); if (s == 5) return texture(uHi5, st);
    if (s == 6) return texture(uHi6, st);
    return texture(uHi7, st);
  }
  vec4 heroSample(int s, vec2 st) {
    if (s == 0) return texture(uHero0, st); if (s == 1) return texture(uHero1, st); if (s == 2) return texture(uHero2, st);
    return texture(uHero3, st);
  }
  void main() {
    fadeDiscard(vAlpha * uOpacityMul);
    vec2 uv = vFace < -0.5 ? vec2(1.0 - vUv.x, vUv.y) : vUv;
    float sq = vCell.w;
    float hgt = sq > 0.5 ? uCell.y * uCellAspect : uCell.y;
    vec2 st = vec2(vCell.y * uCell.x + uv.x * uCell.x, vCell.z * uCell.y + (1.0 - uv.y) * hgt);
    vec3 art = texture(uAtlas, vec3(st, vCell.x)).rgb;
    if (vState.z > -0.5 && vState.w > 0.001 && abs(vFace) > 0.5) {
      float frac = sq > 0.5 ? 0.6666667 : 1.0;
      vec2 hst = vec2(uv.x, 1.0 - (1.0 - uv.y) * frac);
      vec3 hi = hiSample(int(vState.z + 0.5), hst).rgb;
      art = mix(art, hi, vState.w);
    }
    if (vHero.x > -0.5 && vHero.y > 0.001 && abs(vFace) > 0.5) {
      vec3 hr = heroSample(int(vHero.x + 0.5), uv).rgb;
      art = mix(art, hr, vHero.y);
    }
    float aE = vAlpha * uOpacityMul;
    vec3 col = shadePoster(art, vCol, vN, vWP, vUv, vFace, clamp(1.0 - (1.0 - vState.x * 0.9) * clamp(aE, 0.0, 1.0), 0.0, 1.0), vState.y);
    gl_FragColor = vec4(col, 1.0);
    #include <colorspace_fragment>
  }`;

const GVERT = /* glsl */ `
  ${COMMON}
  uniform float uHover;
  varying vec2 vUv; varying vec3 vN; varying vec3 vWP;
  flat varying float vFace;
  void main() {
    vec3 lp = vec3(position.x, position.y, position.z * uThick * 2.0);
    vec4 wp = modelMatrix * vec4(lp, 1.0);
    vec3 c = (modelMatrix * vec4(0.0, 0.0, 0.0, 1.0)).xyz;
    vN = normalize(mat3(modelMatrix) * normal);
    vWP = wp.xyz; vUv = uv; vFace = normal.z;
    gl_Position = projectionMatrix * viewMatrix * wp;
  }`;
const GFRAG = /* glsl */ `
  precision highp float;
  ${COMMON}
  ${FRAG_COMMON}
  uniform sampler2D uMap; uniform vec3 uDom; uniform float uAlpha, uDim, uHover, uSq;
  varying vec2 vUv; varying vec3 vN; varying vec3 vWP; flat varying float vFace;
  void main() {
    fadeDiscard(uAlpha * uOpacityMul);
    vec2 uv = vFace < -0.5 ? vec2(1.0 - vUv.x, vUv.y) : vUv;
    float frac = uSq > 0.5 ? 0.6666667 : 1.0;
    vec3 art = texture(uMap, vec2(uv.x, 1.0 - (1.0 - uv.y) * frac)).rgb;
    float aE = uAlpha * uOpacityMul;
    vec3 col = shadePoster(art, uDom, vN, vWP, vUv, vFace, clamp(1.0 - (1.0 - uDim * 0.9) * clamp(aE, 0.0, 1.0), 0.0, 1.0), uHover);
    gl_FragColor = vec4(col, 1.0);
    #include <colorspace_fragment>
  }`;

/* ------------------------------------------------------------------ field */

export interface PosterEngine extends PosterField {
  tick(now: number, dt: number, camera: THREE.PerspectiveCamera, viewport: { w: number; h: number }): void;
  setBackground(c: THREE.Color): void;
  /** Currently hovered node id (or null); set by the engine from the pointer. */
  readonly hovered: number | null;
  setHover(id: number | null, ndcX?: number, ndcY?: number): void;
  sharp(ids: number[]): void;
  pick(ray: THREE.Ray): number | null;
  uniforms: Record<string, THREE.IUniform>;
  light: THREE.Vector3;
  rimStrength: number;
  hoverAnchor(id: number, camera: THREE.Camera, out: THREE.Vector2): THREE.Vector2;
  clock(): number;
  /** Current drawn pose of poster i: writes position + orientation, returns scale (height) and opacity. Square = albums. */
  poseOf(i: number, pos: THREE.Vector3, q: THREE.Quaternion): { scale: number; opacity: number; aspect: number };
}

export async function createPosterField(world: WorldData, scene: THREE.Scene, onProgress: (f: number) => void = () => {}): Promise<PosterEngine> {
  const mine = world.nodes.slice(0, world.mineCount);
  const n = mine.length;
  const sheetIds = [...new Set(mine.filter((m) => m.poster).map((m) => m.poster![0]))].sort((a, b) => a - b);
  const layerOf = new Map(sheetIds.map((s, i) => [s, i]));
  const { tex, w, h } = await buildArrayTexture(world, sheetIds, onProgress);
  const t0 = performance.now() / 1000;
  const clock = () => performance.now() / 1000 - t0;
  function hash(i: number) { const x = Math.sin(i * 127.1 + 311.7) * 43758.5453; return x - Math.floor(x); }

  const geo = new THREE.InstancedBufferGeometry();
  const box = new THREE.BoxGeometry(1, 1, 1);
  geo.index = box.index;
  geo.setAttribute("position", box.getAttribute("position"));
  geo.setAttribute("normal", box.getAttribute("normal"));
  geo.setAttribute("uv", box.getAttribute("uv"));
  const mkAttr = (size: number, dynamic = true) => {
    const a = new THREE.InstancedBufferAttribute(new Float32Array(n * size), size);
    if (dynamic) a.setUsage(THREE.DynamicDrawUsage);
    return a;
  };
  const aFrom = mkAttr(4), aFromQ = mkAttr(4), aTo = mkAttr(4), aToQ = mkAttr(4), aMisc = mkAttr(4);
  const aTime = mkAttr(4), aCell = mkAttr(4, false), aState = mkAttr(4), aCol = mkAttr(4, false), aHero = mkAttr(2);
  for (let i = 0; i < n; i++) aHero.array[i * 2] = -1;
  const attrs: Record<string, THREE.InstancedBufferAttribute> = { aFrom, aFromQ, aTo, aToQ, aMisc, aTime, aCell, aState, aCol, aHero };
  for (const k in attrs) geo.setAttribute(k, attrs[k]);
  geo.instanceCount = n;

  const aspect = new Float32Array(n);
  const tmpC = new THREE.Color();
  mine.forEach((m, i) => {
    const p = m.poster;
    const sq = m.type === "music" ? 1 : 0;
    aCell.array.set([p ? layerOf.get(p[0])! : 0, p ? p[1] : 0, p ? p[2] : 0, sq], i * 4);
    aspect[i] = sq ? 1 : 2 / 3;
    tmpC.set(m.color || "#555555");
    const accent = (m.rating != null && m.rating >= 9) || m.liked ? (hash(i) < 0.45 ? 1 : 0) : 0;
    aCol.array.set([tmpC.r, tmpC.g, tmpC.b, accent], i * 4);
    aState.array.set([0, 0, -1, 0], i * 4);
    aTime.array.set([0, 1e-4, 1, hash(i + 7)], i * 4);
  });

  const hiTex: THREE.Texture[] = [];
  const hiCanvas: HTMLCanvasElement[] = [];
  const uniforms: Record<string, THREE.IUniform> = {
    uAtlas: { value: tex },
    uCell: { value: new THREE.Vector2(world.atlas.cellW / w, world.atlas.cellH / h) },
    uCellAspect: { value: world.atlas.cellW / world.atlas.cellH },
    uNow: { value: 0 }, uFrame: { value: 0 }, uThick: { value: 0.0 }, uSwirl: { value: 1 },
    uCamPos: { value: new THREE.Vector3() }, uCamQ: { value: new THREE.Vector4(0, 0, 0, 1) },
    uLightDir: { value: new THREE.Vector3(-0.45, 0.7, 0.6).normalize() },
    uBg: { value: new THREE.Color("#050505") }, uTilt: { value: new THREE.Vector2() },
    uRimStrength: { value: 1 }, uBreath: { value: 1 },
    uFogColor: { value: new THREE.Color("#050505") }, uFogP: { value: new THREE.Vector3(10, 100, 0) }, uFogMode: { value: 0 },
    uOpacityMul: { value: 1 },
  };
  for (let s = 0; s < 8; s++) {
    const c = document.createElement("canvas"); c.width = 240; c.height = 360;
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 16; t.generateMipmaps = true; t.minFilter = THREE.LinearMipmapLinearFilter;
    hiCanvas.push(c); hiTex.push(t);
    uniforms["uHi" + s] = { value: t };
    t.needsUpdate = true;
  }
  const mat = new THREE.ShaderMaterial({ uniforms, vertexShader: VERT, fragmentShader: FRAG, side: THREE.FrontSide });
  // plain Mesh over an InstancedBufferGeometry: InstancedMesh would add a mat4 attribute (4 slots) we don't use
  const mesh = new THREE.Mesh(geo as unknown as THREE.BufferGeometry, mat) as unknown as THREE.InstancedMesh;
  mesh.frustumCulled = false;
  mesh.raycast = () => {}; // picking is ours (pick())
  scene.add(mesh);

  /* ---- state ---- */
  const target = makePose(n);
  const stateStart = new Float64Array(n), stateDur = new Float32Array(n).fill(1e-4), stateEase = new Uint8Array(n).fill(1);
  const from = makePose(n);
  const fromBB = new Float32Array(n);
  for (let i = 0; i < n; i++) { // start: an invisible far cloud, so the first morph is an arrival
    const a = hash(i) * 6.283, b = Math.acos(2 * hash(i + 3) - 1), r = 40 + hash(i + 5) * 40;
    from.position.set([Math.sin(b) * Math.cos(a) * r, Math.cos(b) * r * 0.6, Math.sin(b) * Math.sin(a) * r - 20], i * 3);
    from.scale[i] = 0.4; from.opacity[i] = 0;
  }
  copyPose(target, from);
  const dimCur = new Float32Array(n), dimTgt = new Float32Array(n);
  const hoverCur = new Float32Array(n);
  let hovered: number | null = null;
  const tiltTarget = new THREE.Vector2();
  let pending: { resolve: () => void; until: number }[] = [];
  let hiDirty = false;
  const camQ = new THREE.Quaternion();
  let camRef: THREE.PerspectiveCamera = new THREE.PerspectiveCamera();

  function flushPose(i: number) {
    aFrom.array.set([from.position[i * 3], from.position[i * 3 + 1], from.position[i * 3 + 2], from.scale[i]], i * 4);
    aFromQ.array.set([from.quaternion[i * 4], from.quaternion[i * 4 + 1], from.quaternion[i * 4 + 2], from.quaternion[i * 4 + 3]], i * 4);
    aTo.array.set([target.position[i * 3], target.position[i * 3 + 1], target.position[i * 3 + 2], target.scale[i]], i * 4);
    aToQ.array.set([target.quaternion[i * 4], target.quaternion[i * 4 + 1], target.quaternion[i * 4 + 2], target.quaternion[i * 4 + 3]], i * 4);
    aMisc.array.set([from.opacity[i], target.opacity[i], fromBB[i], target.billboard[i]], i * 4);
    aTime.array[i * 4] = stateStart[i]; aTime.array[i * 4 + 1] = stateDur[i]; aTime.array[i * 4 + 2] = stateEase[i];
  }
  function flushAll() {
    for (let i = 0; i < n; i++) flushPose(i);
    for (const a of [aFrom, aFromQ, aTo, aToQ, aMisc, aTime]) a.needsUpdate = true;
  }
  flushAll();

  // CPU mirror of the vertex shader (position, quaternion, scale, opacity; no hover/accent spin)
  const _a = new THREE.Vector3(), _b = new THREE.Vector3(), _ab = new THREE.Vector3(), _c1 = new THREE.Vector3(), _c2 = new THREE.Vector3();
  const _sw = new THREE.Vector3(), _up = new THREE.Vector3(0, 1, 0), _oa = new THREE.Vector3(), _ob = new THREE.Vector3();
  const _qa = new THREE.Quaternion(), _qb = new THREE.Quaternion();
  function evalPose(i: number, now: number, pos: THREE.Vector3, q?: THREE.Quaternion, out?: { scale: number; opacity: number }) {
    const t = Math.min(1, Math.max(0, (now - stateStart[i]) / stateDur[i]));
    const e = t >= 1 ? 1 : EASE[stateEase[i]](t);
    _a.fromArray(from.position, i * 3); _b.fromArray(target.position, i * 3);
    _ab.subVectors(_b, _a);
    const L = _ab.length();
    if (L > 1e-4 && t < 1) {
      const swirl = uniforms.uSwirl.value as number;
      _sw.crossVectors(_up, _ab); const sl = _sw.length();
      if (sl > 1e-5) _sw.multiplyScalar(swirl / sl); else _sw.set(swirl, 0, 0);
      _oa.copy(_a); if (_oa.length() > 1e-3) _oa.normalize(); else _oa.copy(_up);
      _ob.copy(_b); if (_ob.length() > 1e-3) _ob.normalize(); else _ob.copy(_up);
      _c1.copy(_a).addScaledVector(_oa, 0.35 * L).addScaledVector(_sw, 0.25 * L);
      _c2.copy(_b).addScaledVector(_ob, -0.35 * L).addScaledVector(_sw, 0.25 * L);
      const u = 1 - e;
      pos.set(0, 0, 0).addScaledVector(_a, u * u * u).addScaledVector(_c1, 3 * u * u * e).addScaledVector(_c2, 3 * u * e * e).addScaledVector(_b, e * e * e);
    } else pos.copy(_b);
    if (q) {
      if (fromBB[i] > 0.5) _qa.copy(camQ); else _qa.fromArray(from.quaternion, i * 4);
      if (target.billboard[i] > 0.5) _qb.copy(camQ); else _qb.fromArray(target.quaternion, i * 4);
      q.copy(_qa).slerp(_qb, e);
    }
    if (out) {
      out.scale = from.scale[i] + (target.scale[i] - from.scale[i]) * e;
      out.opacity = from.opacity[i] + (target.opacity[i] - from.opacity[i]) * e;
    }
  }

  /* ---- hi-res pool (10 slots, loaded from the hi atlas on demand) ---- */
  const hiSlotOf = new Int16Array(n).fill(-1);
  const slotOwner: number[] = new Array(8).fill(-1);
  const slotLoading: boolean[] = new Array(8).fill(false);
  const hiMix = new Float32Array(n);
  async function loadHi(slot: number, id: number) {
    const hc = mine[id].posterHi; if (!hc) return;
    slotLoading[slot] = true;
    try {
      const img = await loadImage(world.atlasHi.sheets[hc[0]]);
      if (slotOwner[slot] !== id) return;
      const g = hiCanvas[slot].getContext("2d")!;
      g.clearRect(0, 0, 240, 360);
      g.drawImage(img, hc[1] * 240, hc[2] * 360, 240, 360, 0, 0, 240, 360);
      hiTex[slot].needsUpdate = true;
      aState.array[id * 4 + 2] = slot; hiSlotOf[id] = slot; hiMix[id] = 0; hiDirty = true;
    } catch { /* leave base */ } finally { slotLoading[slot] = false; }
  }
  function assignHi(ids: number[]) {
    const want = new Set(ids.slice(0, 16)); // generous keep-set: no thrash at the rank border
    for (const id of ids.slice(0, 8)) {
      if (slotOwner.includes(id)) continue;
      const s = slotOwner.findIndex((o, k) => (o < 0 || !want.has(o)) && !slotLoading[k]);
      if (s < 0) break;
      const prev = slotOwner[s];
      if (prev >= 0) { hiSlotOf[prev] = -1; aState.array[prev * 4 + 2] = -1; aState.array[prev * 4 + 3] = 0; hiMix[prev] = 0; }
      slotOwner[s] = id;
      loadHi(s, id);
    }
    hiDirty = true;
  }
  // hero pool: full-res per-title textures (500x750 / 600x600) for the focused poster and its nexts
  const blank = new THREE.DataTexture(new Uint8Array([20, 20, 20, 255]), 1, 1);
  blank.needsUpdate = true;
  for (let k = 0; k < 4; k++) uniforms["uHero" + k] = { value: blank };
  const heroOwner: number[] = new Array(4).fill(-1);
  const heroBusy: boolean[] = new Array(4).fill(false);
  const heroMix = new Float32Array(n);
  let heroDirty = false;
  function setHero(ids: number[]) {
    const want = ids.slice(0, 4);
    heroOwner.forEach((o, k) => {
      if (o >= 0 && !want.includes(o) && !heroBusy[k]) { aHero.array[o * 2] = -1; aHero.array[o * 2 + 1] = 0; heroMix[o] = 0; heroOwner[k] = -1; heroDirty = true; }
    });
    for (const id of want) {
      if (heroOwner.includes(id)) continue;
      const k = heroOwner.findIndex((o, j) => o < 0 && !heroBusy[j]);
      if (k < 0) break;
      heroOwner[k] = id; heroBusy[k] = true;
      loadImage(`data/hero/${id}.jpg`).then((img) => {
        if (heroOwner[k] !== id) return;
        const t = new THREE.Texture(img);
        t.colorSpace = THREE.SRGBColorSpace; t.generateMipmaps = true; t.minFilter = THREE.LinearMipmapLinearFilter;
        t.magFilter = THREE.LinearFilter; t.anisotropy = 16; t.needsUpdate = true;
        const old = uniforms["uHero" + k].value as THREE.Texture;
        uniforms["uHero" + k].value = t;
        if (old !== blank) old.dispose();
        aHero.array[id * 2] = k; heroMix[id] = 0; heroDirty = true;
      }).catch(() => { if (heroOwner[k] === id) heroOwner[k] = -1; }).finally(() => { heroBusy[k] = false; });
    }
  }
  let sharpIds: number[] = [];
  const _p = new THREE.Vector3(), _v = new THREE.Vector3();
  function selectHi(camera: THREE.PerspectiveCamera, vh: number, now: number) {
    const proj: { id: number; px: number }[] = [];
    const fovF = vh / (2 * Math.tan((camera.fov * Math.PI) / 360));
    camera.getWorldDirection(_v);
    for (let i = 0; i < n; i++) {
      if (!mine[i].posterHi || target.opacity[i] < 0.1) continue;
      evalPose(i, now, _p);
      const depth = (_p.x - camera.position.x) * _v.x + (_p.y - camera.position.y) * _v.y + (_p.z - camera.position.z) * _v.z;
      if (depth <= 0.1) continue;
      const px = Math.max(from.scale[i], target.scale[i]) * fovF / depth;
      if (px < 70) continue;
      _p.project(camera);
      if (Math.abs(_p.x) > 1.15 || Math.abs(_p.y) > 1.15) continue;
      proj.push({ id: i, px: px * (1.2 - Math.hypot(_p.x, _p.y) * 0.3) });
    }
    proj.sort((a, b) => b.px - a.px);
    assignHi([...new Set([...sharpIds, ...(hovered != null && hovered < n ? [hovered] : []), ...proj.map((p) => p.id)])]);
  }

  /* ---- ghosts (unlogged titles) ---- */
  type G = Ghost & { face(q: THREE.Quaternion): void; mat: THREE.ShaderMaterial; hv: number; hvT: number; fade(dt: number): void; dying: boolean };
  const ghostGeo = new THREE.BoxGeometry(1, 1, 1);
  const ghosts: G[] = [];
  const blankTex = new THREE.DataTexture(new Uint8Array([60, 60, 60, 255]), 1, 1);
  blankTex.colorSpace = THREE.SRGBColorSpace; blankTex.needsUpdate = true;
  const ghostShared: Record<string, THREE.IUniform> = {};
  for (const k of ["uNow", "uFrame", "uCamPos", "uCamQ", "uLightDir", "uBg", "uRimStrength", "uFogColor", "uFogP", "uFogMode", "uThick", "uBreath", "uTilt", "uOpacityMul"]) ghostShared[k] = uniforms[k];
  function ghost(id: number, at: THREE.Vector3, opts: { scale?: number; opacity?: number; quaternion?: THREE.Quaternion; billboard?: boolean } = {}): Ghost {
    const node = world.nodes[id];
    const sq = node.type === "music";
    const material = new THREE.ShaderMaterial({
      uniforms: { ...ghostShared, uMap: { value: blankTex }, uDom: { value: new THREE.Color(node.color || "#666666") }, uAlpha: { value: 0 }, uDim: { value: 0 }, uHover: { value: 0 }, uSq: { value: sq ? 1 : 0 } },
      vertexShader: GVERT, fragmentShader: GFRAG,
    });
    const m = new THREE.Mesh(ghostGeo, material);
    let sc = opts.scale ?? 1;
    m.scale.set(sc * (sq ? 1 : 2 / 3), sc, sc);
    m.position.copy(at);
    if (opts.quaternion) m.quaternion.copy(opts.quaternion);
    m.frustumCulled = false;
    scene.add(m);
    m.userData.nodeId = id;
    let targetA = opts.opacity ?? 0.95;
    if (node.poster) {
      loadImage(world.atlas.sheets[node.poster[0]]).then((img) => {
        if (g.dying) return;
        const cw = world.atlas.cellW, ch = world.atlas.cellH;
        const c = document.createElement("canvas");
        c.width = cw * 2; c.height = ch * 2;
        const cx = c.getContext("2d")!; cx.imageSmoothingQuality = "high";
        cx.drawImage(img, node.poster![1] * cw, node.poster![2] * ch, cw, ch, 0, 0, c.width, c.height);
        const t = new THREE.CanvasTexture(c);
        t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 8;
        material.uniforms.uMap.value = t;
      }).catch(() => {});
    }
    const g: G = {
      id, mesh: m as THREE.Object3D, mat: material, hv: 0, hvT: 0, dying: false,
      set(p: THREE.Vector3, scale?: number, opacity?: number) {
        m.position.copy(p);
        if (scale != null) { sc = scale; m.scale.set(scale * (sq ? 1 : 2 / 3), scale, scale); }
        if (opacity != null) targetA = opacity;
      },
      remove() { if (g.dying) return; g.dying = true; targetA = 0; delete m.userData.nodeId; },
      face(cq: THREE.Quaternion) { if (opts.billboard !== false && !opts.quaternion) m.quaternion.copy(cq); },
      fade(dt: number) {
        const u = material.uniforms;
        u.uAlpha.value += (targetA - u.uAlpha.value) * (1 - Math.exp(-dt * 9));
        g.hv += (g.hvT - g.hv) * (1 - Math.exp(-dt * (g.hvT > g.hv ? 12 : 6)));
        u.uHover.value = g.hv;
        if (g.dying && u.uAlpha.value < 0.02) {
          scene.remove(m);
          const t = u.uMap.value as THREE.Texture; if (t !== blankTex) t.dispose();
          material.dispose();
          const k = ghosts.indexOf(g); if (k >= 0) ghosts.splice(k, 1);
        }
      },
    };
    ghosts.push(g);
    const live = ghosts.filter((x) => !x.dying);
    for (let k = 0; k < live.length - 48; k++) live[k].remove();
    return g;
  }

  /* ---- morph ---- */
  function morphTo(pose: PoseBuffer, opts: MorphOptions = {}) {
    const now = clock();
    const d = opts.duration ?? 1.6, st = opts.stagger ?? 0.8;
    const eid = EASE_ID[opts.ease ?? "inOutExpo"] ?? 1;
    const curPos = new Float32Array(n * 3), curQ = new Float32Array(n * 4), curS = new Float32Array(n), curO = new Float32Array(n);
    const pv = new THREE.Vector3(), pq = new THREE.Quaternion(), o = { scale: 1, opacity: 1 };
    for (let i = 0; i < n; i++) {
      evalPose(i, now, pv, pq, o);
      pv.toArray(curPos, i * 3); pq.toArray(curQ, i * 4); curS[i] = o.scale; curO[i] = o.opacity;
    }
    from.position.set(curPos); from.quaternion.set(curQ); from.scale.set(curS); from.opacity.set(curO);
    fromBB.fill(0);
    copyPose(target, pose);
    let order = opts.order;
    if (!order) { // unwind from the heart of the current view outward (rank of screen radius)
      const rad = new Float32Array(n);
      camRef.updateMatrixWorld();
      for (let i = 0; i < n; i++) {
        pv.fromArray(curPos, i * 3).project(camRef);
        rad[i] = Math.hypot(pv.x, pv.y) + (pv.z > 1 ? 3 : 0);
      }
      const idx = Array.from({ length: n }, (_, i) => i).sort((a, b) => rad[a] - rad[b]);
      const o2 = new Float32Array(n);
      idx.forEach((id, r) => (o2[id] = r / (n - 1)));
      order = o2;
    }
    for (let i = 0; i < n; i++) {
      stateStart[i] = now + order[i] * st + (hash(i + 11) - 0.5) * 0.03; stateDur[i] = d; stateEase[i] = eid;
    }
    flushAll();
    return new Promise<void>((resolve) => pending.push({ resolve, until: now + d + st + 0.05 }));
  }

  let frame = 0, lastSel = 0;
  const _pv = new THREE.Vector3(), _hq = new THREE.Quaternion(), _hq2 = new THREE.Quaternion();
  const _nrm = new THREE.Vector3(), _rel = new THREE.Vector3(), _loc = new THREE.Vector3();
  const _o = { scale: 1, opacity: 1 };
  const _poseOut = { scale: 1, opacity: 1 };

  const api: PosterEngine = {
    count: n, mesh, target, uniforms, light: uniforms.uLightDir.value as THREE.Vector3, rimStrength: 1,
    get hovered() { return hovered; },
    makePose: () => makePose(n),
    morphTo,
    follow(pose) {
      const now = clock();
      for (let i = 0; i < n; i++) {
        if (now - stateStart[i] >= stateDur[i]) {
          for (let k = 0; k < 3; k++) from.position[i * 3 + k] = pose.position[i * 3 + k];
          for (let k = 0; k < 4; k++) from.quaternion[i * 4 + k] = pose.quaternion[i * 4 + k];
          from.scale[i] = pose.scale[i]; from.opacity[i] = pose.opacity[i]; fromBB[i] = pose.billboard[i];
        }
      }
      copyPose(target, pose);
      flushAll();
    },
    current(i, out) { evalPose(i, clock(), out); return out; },
    poseOf(i, pos, q) { evalPose(i, clock(), pos, q, _poseOut); return { scale: _poseOut.scale, opacity: _poseOut.opacity, aspect: aspect[i] }; },
    ghost,
    clearGhosts() { for (const g of ghosts) g.remove(); },
    dim(amount, except = []) {
      const ex = new Set(except);
      for (let i = 0; i < n; i++) dimTgt[i] = amount > 0 && !ex.has(i) ? amount : 0;
    },
    setBackground(c) { (uniforms.uBg.value as THREE.Color).copy(c); },
    setHover(id, nx = 0, ny = 0) {
      hovered = id;
      for (const g of ghosts) g.hvT = !g.dying && g.id === id ? 1 : 0;
      tiltTarget.set(nx, ny);
    },
    sharp(ids) { sharpIds = ids.filter((i) => i < n); lastSel = 0; setHero(sharpIds); },
    pick(ray) {
      const now = clock();
      let best = -1, bestT = Infinity;
      for (let i = 0; i < n; i++) {
        if (target.opacity[i] < 0.05) continue;
        evalPose(i, now, _pv, _hq, _o);
        if (_o.opacity < 0.2) continue;
        _nrm.set(0, 0, 1).applyQuaternion(_hq);
        const den = _nrm.dot(ray.direction);
        if (Math.abs(den) < 1e-5) continue;
        _rel.subVectors(_pv, ray.origin);
        const t = _rel.dot(_nrm) / den;
        if (t <= 0 || t >= bestT) continue;
        _hq2.copy(_hq).invert();
        _loc.copy(ray.direction).multiplyScalar(t).add(ray.origin).sub(_pv).applyQuaternion(_hq2).divideScalar(_o.scale);
        if (Math.abs(_loc.x) <= aspect[i] * 0.5 && Math.abs(_loc.y) <= 0.5) { best = i; bestT = t; }
      }
      return best >= 0 ? best : null;
    },
    hoverAnchor(id, camera, out) {
      evalPose(id, clock(), _pv, undefined, _o);
      const right = _loc.setFromMatrixColumn(camera.matrixWorld, 0);
      _pv.addScaledVector(right, aspect[id] * _o.scale * 0.5).project(camera);
      return out.set(_pv.x * 0.5 + 0.5, -_pv.y * 0.5 + 0.5);
    },
    clock,
    tick(now, dt, camera, vp) {
      camRef = camera;
      frame++;
      camera.getWorldQuaternion(camQ);
      const u = uniforms;
      u.uNow.value = clock(); u.uFrame.value = frame % 997;
      (u.uCamPos.value as THREE.Vector3).copy(camera.position);
      (u.uCamQ.value as THREE.Vector4).set(camQ.x, camQ.y, camQ.z, camQ.w);
      (u.uTilt.value as THREE.Vector2).lerp(tiltTarget, 1 - Math.exp(-dt * 10));
      u.uRimStrength.value = api.rimStrength;
      const f = scene.fog as THREE.Fog | THREE.FogExp2 | null;
      if (f) {
        (u.uFogColor.value as THREE.Color).copy(f.color);
        if ((f as THREE.Fog).isFog) { u.uFogMode.value = 1; (u.uFogP.value as THREE.Vector3).set((f as THREE.Fog).near, (f as THREE.Fog).far, 0); }
        else { u.uFogMode.value = 2; (u.uFogP.value as THREE.Vector3).set(0, 0, (f as THREE.FogExp2).density); }
      } else u.uFogMode.value = 0;

      let changed = false;
      const kd = 1 - Math.exp(-dt * 6);
      for (let i = 0; i < n; i++) {
        const dc = dimCur[i], dg = dimTgt[i];
        if (dc !== dg) { const nv = Math.abs(dg - dc) < 0.002 ? dg : dc + (dg - dc) * kd; dimCur[i] = nv; aState.array[i * 4] = nv; changed = true; }
        const ht = hovered === i ? 1 : 0, hc = hoverCur[i];
        if (hc !== ht) { const nv = Math.abs(ht - hc) < 0.002 ? ht : hc + (ht - hc) * (1 - Math.exp(-dt * (ht > hc ? 12 : 6))); hoverCur[i] = nv; aState.array[i * 4 + 1] = nv; changed = true; }
        if (hiSlotOf[i] >= 0 && hiMix[i] < 1) { hiMix[i] = Math.min(1, hiMix[i] + dt / 0.4); aState.array[i * 4 + 3] = hiMix[i] * hiMix[i] * (3 - 2 * hiMix[i]); changed = true; }
      }
      for (let k = 0; k < 4; k++) {
        const id = heroOwner[k];
        if (id >= 0 && aHero.array[id * 2] === k && heroMix[id] < 1) { heroMix[id] = Math.min(1, heroMix[id] + dt / 0.45); aHero.array[id * 2 + 1] = heroMix[id] * heroMix[id] * (3 - 2 * heroMix[id]); heroDirty = true; }
      }
      if (heroDirty) { aHero.needsUpdate = true; heroDirty = false; }
      if (changed || hiDirty) { aState.needsUpdate = true; hiDirty = false; }
      if (now - lastSel > 0.16) { lastSel = now; selectHi(camera, vp.h, clock()); }
      const cl = clock();
      pending = pending.filter((p) => (cl >= p.until ? (p.resolve(), false) : true));
      for (const g of [...ghosts]) { g.face(camQ); g.fade(dt); }
    },
  };
  return api;
}
