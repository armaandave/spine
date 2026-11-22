# TECH.md — how to make Spine World feel like Lusion / Active Theory / igloo

Written by the tech researcher. Verified against this repo: three 0.187.0, postprocessing 6.39.6,
troika-three-text 0.53.1, M1 Pro. **Everything marked [tested] was compiled and run on the real
GPU (Chrome for Testing, ANGLE/Metal) in a scratch harness, not just read.** Scratch files live in
`/tmp/claude-501/tech/web/` (`poster-gpu.js`, `lines-gpu.js`, `t2.html`) and are copy-pasteable.

---------------------------------------------------------------------------------------------

## 0. READ FIRST — five things that will silently ruin the look

1. **Background colour is wrong today [tested].** `renderer.setClearColor("#050505")` renders as
   **rgb(38,38,38)** (#808080 renders as 188) on the default framebuffer AND through the composer:
   the clear colour gets sRGB-encoded twice. `scene.background = new THREE.Color("#050505")` renders
   exactly 5,5,5 in both paths. **Engine agent: replace every `setClearColor` with
   `scene.background`** (engine.ts `switchTo` does it per mode; use `(scene.background as THREE.Color).set(next.background)`).
   This is probably why the dark modes look grey, not black. Light mode (`#f2efe8`) is shifted too.
2. **Colour space with a composer.** The current poster FS writes raw texture values to `gl_FragColor`
   with no decode/encode. Fine direct-to-screen, **wrong the moment a composer is added** (washed out).
   Fix = sRGB-tag the texture (`tex.colorSpace = THREE.SRGBColorSpace` → hardware decode, and correct
   linear mip filtering) and end every custom fragment shader with `#include <colorspace_fragment>`.
   [tested: a (0,100,200) texel comes out exactly (0,100,200) through a HalfFloat composer.]
3. **Texture memory.** Base atlas = 27 sheets × 2016² RGBA = **439 MB** as one array texture (plus 33%
   for mips); `posterHi` sheets are 17 × 2400×2160 = 880 MB. Don't upload either whole. See §1.
4. **Atlas cells bleed at mip levels** (neighbouring posters smear into each other with mipmaps +
   anisotropy). One array LAYER PER POSTER removes that for good (§1).
5. **InstancedMesh.raycast uses `instanceMatrix`.** The moment posters move on the GPU (§2) that
   matrix is stale → use GPU picking (§7, [tested]).

---------------------------------------------------------------------------------------------

## 1. Posters — render 990 (to ~8k) sharp textured quads

**Recommendation:** one `InstancedBufferGeometry` (a thin `BoxGeometry(1,1,0.012)`, 24 verts / 12 tris:
real thickness, lit edges, back face), **custom ShaderMaterial, OPAQUE** (no `transparent`),
`alphaToCoverage: true` for rounded-corner AA, **stochastic (IGN-dither) fade instead of alpha blending**,
one `sampler2DArray` with **one layer per poster**. One draw call, 990×12 = 12k tris — trivial; 8k posters
= 96k tris, still trivial. The cost is fill rate + texture memory, not geometry.

Why not transparent + blending: 990 overlapping blended quads need per-frame back-to-front sorting
(can't sort one instanced draw) → popping/halo artifacts. Opaque + depthWrite + dither-discard is
order-independent, and the film grain hides the dither. (Lusion/Active Theory both avoid sorted
transparency for hero objects; they dither or use A2C.)

### Texture plan (concrete)
| tier | size | what | memory |
|---|---|---|---|
| base array | 160×240 RGBA8, 1 layer/poster, mipmaps, aniso 8 | his 990, always resident | 990×160×240×4 = 152 MB (+33% mips ≈ 203 MB) |
| hi array | 240×360 (or 480×720 if we re-export), 16 layers LRU | focused poster + its 5 `next` + hovered | 16×240×360×4 = 5.5 MB |
| ghosts | existing CanvasTexture path | ≤48 | small |

- Build layers from the **hi atlas** (240×360 cells, his posters only) downsampled to 160×240 with
  `createImageBitmap(img, sx, sy, 240, 360, { resizeWidth: 160, resizeHeight: 240, resizeQuality: "high" })`
  then `ctx.drawImage` into one reusable 160×240 canvas and `getImageData` into the big `Uint8Array`.
  Albums: keep their 1:1 art in the TOP of the layer exactly as today (shader crops v by 2/3).
  Pre-fill every layer with the node's dominant `color` so unloaded posters show colour not black;
  upload in **2–3 batches** (each `needsUpdate` regenerates all mips ≈ 20–40 ms on M1 — never per layer).
- `tex.colorSpace = SRGBColorSpace; tex.minFilter = LinearMipmapLinearFilter; tex.generateMipmaps = true;
  tex.anisotropy = Math.min(8, renderer.capabilities.getMaxAnisotropy()); tex.flipY` stays false (DataArrayTexture).
  Do NOT set `unpackAlignment` games; width 160 is fine.
- For 8k posters: layers would be 450 MB at 96×144 → use 4×4-poster sheets per layer WITH a 2-px gutter
  (bleed-free mips down to 4×4) or only keep the nearest ~1.5k resident. Not needed for 990.
- Hi-res swap: write the 240×360 image into the hi array layer (`DataArrayTexture` of 16 layers +
  `addLayerUpdate(i)` [exists in r187: `tex.layerUpdates`]; disable mipmaps on that texture, plain
  `LinearFilter` is enough for a focus-size poster), set `aTex.z = layer`, then tween `aTex.w` 0→1
  (≈0.35 s) — the shader cross-fades (see FS). 16-slot LRU keyed by node id.

### KTX2/Basis — verdict: **skip tonight**
- `basisu`, `toktx`, `ktx` are **not installed** (`which` empty; no brew formula pulled in — don't install, the box is shared with prod).
- Three ships `examples/jsm/loaders/KTX2Loader.js` + `examples/jsm/libs/basis/basis_transcoder.{js,wasm}` (present),
  and KTX2 *array* textures are supported by it, so the runtime half exists. The missing half is an
  offline encoder: `npm i -D ktx2-encoder` (wasm UASTC/ETC1S encoder in node) is the path — **untested, not tonight**.
- Win would be ~6–8× less VRAM (ASTC 4×4 on M-series = 8 bpp vs 32) and faster upload. At 203 MB RGBA8 we are fine on 16 GB unified
  memory. Revisit only for the 8k-poster scenario. Never use PNG→KTX at runtime in the browser.

### Material (opaque, instanced, thickness, rounded corners, edge light, hover tilt) [tested: compiles, renders, picks]
Contract with the CPU side: per-instance attributes `aFromP aToP aFromQ aToQ aFromS aToS aMorph aTex`
(see §2). `gl_InstanceID` is available (three emits GLSL ES 3.00 for ShaderMaterial on WebGL2).

```js
import * as THREE from "three";

export const POSTER_VERT = /* glsl */ `
  // per-instance (InstancedBufferAttribute, DynamicDrawUsage)
  attribute vec3 aFromP;  attribute vec3 aToP;
  attribute vec4 aFromQ;  attribute vec4 aToQ;
  attribute vec4 aFromS;  attribute vec4 aToS;   // x height, y opacity, z billboard, w aspect(w/h)
  attribute vec4 aMorph;  // x start (s), y duration (s), z curl (-1..1), w seed 0..1
  attribute vec4 aTex;    // x layer, y square(0/1), z hiLayer (-1 = none), w hiMix 0..1
  uniform float uTime;
  uniform int   uEase;    // 0 inOutExpo 1 outExpo 2 outQuart 3 inOutCubic 4 outCubic
  uniform vec4  uCamQ;    // camera quaternion (for billboards)
  uniform float uHover;   // hovered instance id, -1 none
  uniform float uHoverMix;// 0..1 smoothed on CPU
  uniform vec2  uTilt;    // pointer offset on the hovered poster, -1..1

  varying vec2 vUv; varying vec3 vWorldN; varying vec3 vWorld;
  flat varying float vOpacity; flat varying vec4 vTex; flat varying float vFace;
  flat varying float vAspect; flat varying float vSeed; flat varying float vId; flat varying float vHov;

  float ease(float t) {
    if (uEase == 0) return t <= 0. ? 0. : t >= 1. ? 1. : t < .5 ? pow(2., 20.*t-10.)*.5 : (2. - pow(2., -20.*t+10.))*.5;
    if (uEase == 1) return t >= 1. ? 1. : 1. - pow(2., -10.*t);
    if (uEase == 2) return 1. - pow(1.-t, 4.);
    if (uEase == 3) return t < .5 ? 4.*t*t*t : 1. - pow(-2.*t+2., 3.)*.5;
    return 1. - pow(1.-t, 3.);
  }
  vec3 qrot(vec3 v, vec4 q) { return v + 2.0 * cross(q.xyz, cross(q.xyz, v) + q.w * v); }
  vec4 nlerp(vec4 a, vec4 b, float t) { if (dot(a,b) < 0.) b = -b; return normalize(mix(a, b, t)); }

  void main() {
    float t  = clamp((uTime - aMorph.x) / max(aMorph.y, 1e-4), 0., 1.);
    float e  = ease(t);
    float arc = sin(3.14159265 * t);                      // 0 at both ends, 1 mid-flight
    // curved path: quadratic bezier, control point pushed sideways + up proportional to distance
    vec3 d = aToP - aFromP; float L = length(d);
    vec3 side = cross(d, vec3(.31, 1., .17));
    side = side / max(length(side), 1e-4);
    vec3 ctrl = .5*(aFromP + aToP) + side * L * .22 * aMorph.z + vec3(0., L * .10 * abs(aMorph.z), 0.);
    vec3 c = mix(mix(aFromP, ctrl, e), mix(ctrl, aToP, e), e);

    vec4 S = mix(aFromS, aToS, e);                         // height, opacity, billboard, aspect
    float h = S.x * (1. + .12 * arc);                      // posters swell slightly in flight
    vec4 q = nlerp(aFromQ, aToQ, e);
    q = nlerp(q, uCamQ, S.z);                              // billboard blend
    float roll = arc * aMorph.z * .5;                      // tumble in flight (radians)
    q = normalize(vec4(q.xyz * cos(roll*.5) + q.w*vec3(0.,0.,sin(roll*.5)) + cross(q.xyz, vec3(0.,0.,sin(roll*.5))), q.w*cos(roll*.5) - q.z*sin(roll*.5)));

    vec3 p = position * vec3(h * S.w, h, h);               // local; box depth = thickness * h
    // idle breathing + hover lean (local space)
    float hov = (float(gl_InstanceID) == uHover) ? uHoverMix : 0.;
    float sway = sin(uTime * .6 + aMorph.w * 6.2831) * .025;
    float ay = sway + uTilt.x * .35 * hov, ax = -uTilt.y * .35 * hov;
    mat3 ry = mat3(cos(ay),0.,-sin(ay), 0.,1.,0., sin(ay),0.,cos(ay));
    mat3 rx = mat3(1.,0.,0., 0.,cos(ax),sin(ax), 0.,-sin(ax),cos(ax));
    p = ry * rx * p; p.z += .08 * h * hov;
    vec3 n = ry * rx * normal;

    vec3 world = qrot(p, q) + c;
    vWorld = (modelMatrix * vec4(world, 1.)).xyz;
    vWorldN = normalize(mat3(modelMatrix) * qrot(n, q));
    vUv = uv; vOpacity = S.y; vTex = aTex; vAspect = S.w; vSeed = aMorph.w; vId = float(gl_InstanceID); vHov = hov;
    vFace = abs(normal.z) > .5 ? sign(normal.z) : 0.;      // +1 front, -1 back, 0 edge
    gl_Position = projectionMatrix * viewMatrix * vec4(vWorld, 1.);
  }`;

export const POSTER_FRAG = /* glsl */ `
  precision highp float; precision highp sampler2DArray;
  uniform sampler2DArray uTex;     // 990 layers of the 160x240 posters (sRGB8_ALPHA8, mipmapped)
  uniform sampler2DArray uHiTex;   // 16-layer LRU of 240x360 (or 480x720) posters for the focused ones
  uniform float uTime; uniform vec3 uLightDir; uniform float uSheen; uniform vec3 uEdge;
  varying vec2 vUv; varying vec3 vWorldN; varying vec3 vWorld;
  flat varying float vOpacity; flat varying vec4 vTex; flat varying float vFace;
  flat varying float vAspect; flat varying float vSeed; flat varying float vId; flat varying float vHov;

  float ign(vec2 p) { return fract(52.9829189 * fract(dot(p, vec2(.06711056, .00583715)))); }

  void main() {
  #ifdef PICK
    if (vOpacity < .5) discard;
    float id = vId + 1.;
    gl_FragColor = vec4(mod(id, 256.), mod(floor(id / 256.), 256.), floor(id / 65536.), 255.) / 255.;
    return;
  #else
    if (vOpacity < ign(gl_FragCoord.xy)) discard;              // stochastic fade: no sorting, depthWrite stays on
    vec3 N = normalize(vWorldN), V = normalize(cameraPosition - vWorld);
    vec3 col;
    float cover = 1.;
    if (vFace > .5) {
      vec2 st = vec2(vUv.x, (1. - vUv.y) * (vTex.y > .5 ? .6666667 : 1.));
      vec3 lo = texture(uTex, vec3(st, vTex.x)).rgb;
      if (vTex.z >= 0.) lo = mix(lo, texture(uHiTex, vec3(st, vTex.z)).rgb, vTex.w);
      col = lo;
      // rounded corners (radius in height units), AA through alpha-to-coverage
      vec2 half_ = vec2(vAspect, 1.) * .5, p = (vUv - .5) * vec2(vAspect, 1.);
      float r = .012; vec2 q = abs(p) - half_ + r;
      float dist = length(max(q, 0.)) + min(max(q.x, q.y), 0.) - r;
      cover = 1. - smoothstep(-fwidth(dist), fwidth(dist), dist);
      // hairline inner rim + sheen that sweeps with view angle and hover
      float rim = smoothstep(.006, .0, -dist) * .10;
      float band = pow(max(0., 1. - abs(dot(N, V)) * 1.0), 3.) * .0;           // (grazing sheen off by default)
      float sweep = smoothstep(.15, .0, abs(fract(dot(vUv, vec2(.6, .8)) - uTime * .05 + vSeed) - .5) - .35);
      col += (rim + band + sweep * uSheen * (.25 + vHov)) * vec3(1.);
    } else if (vFace < -.5) {
      col = vec3(.16);                                          // paper back
    } else {
      col = uEdge * (.35 + .65 * max(dot(N, normalize(uLightDir)), 0.));   // lit edge = visible thickness
    }
    gl_FragColor = vec4(col, cover);
    #include <colorspace_fragment>
  #endif
  }`;

export function makePosterMaterial(tex, hiTex) {
  return new THREE.ShaderMaterial({
    uniforms: {
      uTex: { value: tex }, uHiTex: { value: hiTex }, uTime: { value: 0 }, uEase: { value: 0 },
      uCamQ: { value: new THREE.Vector4(0, 0, 0, 1) }, uHover: { value: -1 }, uHoverMix: { value: 0 },
      uTilt: { value: new THREE.Vector2() }, uLightDir: { value: new THREE.Vector3(.4, .8, .6) },
      uSheen: { value: .12 }, uEdge: { value: new THREE.Color(.85, .85, .82) },
    },
    vertexShader: POSTER_VERT, fragmentShader: POSTER_FRAG,
    alphaToCoverage: true, transparent: false, depthWrite: true, side: THREE.FrontSide,
  });
}
```

CPU glue (geometry + attributes):

```ts
const box = new THREE.BoxGeometry(1, 1, 0.012);                    // thickness = 1.2% of poster height (scaled in VS)
const geo = new THREE.InstancedBufferGeometry();
geo.index = box.index; for (const k of ["position", "normal", "uv"]) geo.setAttribute(k, box.getAttribute(k));
geo.instanceCount = N;
const inst = (n: number, dyn = true) => { const a = new THREE.InstancedBufferAttribute(new Float32Array(N * n), n); if (dyn) a.setUsage(THREE.DynamicDrawUsage); return a; };
for (const [k, n] of [["aFromP",3],["aToP",3],["aFromQ",4],["aToQ",4],["aFromS",4],["aToS",4],["aMorph",4],["aTex",4]] as const) geo.setAttribute(k, inst(n));
const mesh = new THREE.Mesh(geo, makePosterMaterial(baseTex, hiTex));   // a plain Mesh with instanceCount, NOT InstancedMesh (no instanceMatrix)
mesh.frustumCulled = false;
// per frame:  mat.uniforms.uTime.value = now; mat.uniforms.uCamQ.value.set(q.x,q.y,q.z,q.w) (camera.quaternion)
```
(`types.ts` types `posters.mesh` as `THREE.InstancedMesh`: keep that by creating `new THREE.InstancedMesh(box, mat, N)` and adding the same
InstancedBufferAttributes to `box` — our shader ignores `instanceMatrix` (don't call setMatrixAt); remember `instanceMatrix` is then stale, so `raycast` must not be used: GPU pick (§7).)

- **Aspect** is `S.w` per instance (2/3 or 1 for albums) so `scale[i]` stays the HEIGHT as in types.ts.
- Overdraw: 990 opaque quads, early-z helps; at the 4k-ish canvas (3456×2234 on a 16" at dpr 2) fill is the cost → §8 adaptive dpr.
- Edge thickness catches light because side faces use `uEdge * lambert`; back face is dark paper. Visible mostly when tilted/hovered/in flight.
- A sheen band sweeps slowly across each poster and brightens on hover; keep `uSheen` ≤ 0.15 (BRIEF: "never muddy the posters").
- Hover tilt: CPU eases `uHoverMix` (0→1 over ~0.25 s, critically damped) and `uTilt` (pointer offset inside the poster, −1..1),
  sets `uHover = id`. Only one poster ever tilts; costs nothing.

---------------------------------------------------------------------------------------------

## 2. Morph transitions — GPU, per-instance delay, curved paths

**Recommendation: GPU.** The current CPU loop does 990 × (3 lerps + slerp + compose + setMatrixAt) + re-uploads a 63 KB
matrix buffer every frame (fine at 990, ~8× worse at 8k, and it's all main-thread JS fighting the render loop at 120 fps).
On the GPU the morph is a vertex-shader formula of (`uTime`, per-instance start/duration/curl): zero per-frame CPU work
while posters fly, the path is a real curve, and stagger/ease/tumble are free. The shader above implements it:
- quadratic Bézier whose control point is pushed sideways (`curl`, random −1..1 per poster → swirl) and up;
- ease selected by `uEase` (inOutExpo default; same five names as `MorphOptions.ease`);
- scale swell + roll tumble `sin(πt)` mid-flight (posters feel thrown, not slid);
- billboard blended in with an nlerp to the camera quaternion (`uCamQ`).

CPU contract (keeps `types.ts` intact):

```ts
// state: arrays fromP,toP (3n) fromQ,toQ (4n) fromS,toS (4n) [h,opacity,billboard,aspect]; aMorph (4n) = [start,dur,curl,seed]
function morphTo(pose: PoseBuffer, o: MorphOptions = {}) {
  const now = clock();                       // seconds, same clock as uTime (use time since engine start → float32-safe)
  freezeCurrent(now);                        // 1) evaluate each poster's CURRENT state on the CPU ONCE → write into aFrom*  (so interrupting a morph is seamless)
  for (let i = 0; i < n; i++) { /* 2) copy pose → aTo* (+ aspect, billboard) */ }
  const order = o.order ?? byDistance();     // same default as today
  for (let i = 0; i < n; i++) { aMorph[i*4] = now + order[i] * (o.stagger ?? .6); aMorph[i*4+1] = o.duration ?? 1.4; }
  mat.uniforms.uEase.value = EASE_IDX[o.ease ?? "inOutExpo"];
  for (const a of attrs) a.needsUpdate = true;
  return new Promise<void>(res => pending.push({ res, until: now + (o.duration ?? 1.4) + (o.stagger ?? .6) }));
}
// follow(pose): write ONLY aTo* (P,Q,S) for posters whose t>=1 — settled posters have e===1 so the shader shows aTo directly.
//               Mid-flight posters chase the new target smoothly (the Bézier end moves). Cost: 990×11 floats/frame, trivial.
// current(i,out): CPU mirror of the VS formula (ease + bezier) — only called for a few posters/frame (line endpoints, panel).
```
Gotchas: keep `curl` constant per poster between morphs so paths look intentional; `uTime` must be the same clock as `aMorph.x`;
mirror the ease + bezier math in TS once (`evalPos(i, now, out)`) and use it for both `freezeCurrent` and `current()`.
Optional spice: `aMorph.z` can be set per mode (e.g. TUNNEL curl=0 for straight "warp", GLOBE curl=1 for swirl).

---------------------------------------------------------------------------------------------

## 3. Lines — anti-aliased constant-width hairlines, curves, depth fade

**Recommendation: own instanced-ribbon shader (below), not Line2.** [tested: compiles, renders, AA clean]
- One draw call for ALL edges. Each edge = one instance (`aP0`, `aC` control, `aP1` → quadratic Bézier = curved arcs for free,
  evaluated in the VS), geometry = a 2×(SEG+1)-vertex strip. 5,000 edges × SEG 24 = 240k tris: trivial at 120 fps.
- Constant PIXEL width: the ribbon is offset in clip space by `halfWidthPx * 2 / resolution`, so 1.0 = a true device-pixel hairline at any
  distance/dpr. Anti-aliasing by analytic coverage (`clamp(halfW − |d| + .5)`), exact for sub-pixel widths (0.6 px looks like a fainter, not a thinner-aliased line).
- Depth fade `uFade` (view-space distance, near→far alpha 1→0) → lines dissolve into black/paper with depth, like igloo/Lusion.
- Per-edge alpha, width scale, and **draw-on reveal** (`uProgress` 0→1 with per-edge delay `aMeta.y`: lines grow out from `aP0`; gorgeous for "next" lines on focus).
- Pass `uRes` = `renderer.getDrawingBufferSize()` (device px) on every resize/dpr change. Use `uWidth = 1.0` (maybe `devicePixelRatio >= 2 ? 1.0 : 1.25`).
- `depthWrite:false`, `transparent:true`, `renderOrder = 1` (after the opaque posters). Additive blending on dark (`blending: THREE.AdditiveBlending`), normal blending with dark ink on the paper theme.
- Light bloom: output colour × 2–3 on "light" lines (HDR, > 1) and the composer's bloom threshold 1.0 picks only those (§5).

`LineSegments2`/`LineMaterial` (examples/jsm/lines) is the fallback: [tested compiles] `new LineMaterial({ linewidth: 1, worldUnits: false, transparent: true, opacity: .4, depthWrite:false })`,
`mat.resolution.copy(renderer.getDrawingBufferSize(v))` on every resize, `LineSegmentsGeometry.setPositions(flatArray)`. Straight segments only (you'd tessellate arcs yourself),
no per-segment alpha, no depth fade, `linewidth` is in CSS px at dpr 1 semantics — fine for a few hundred lines, worse for the look. `THREE.Line` is always 1 device px, aliased, no control: don't.

```js
import * as THREE from "three";

/** One draw call for E quadratic-bezier hairlines, each a camera-facing ribbon of SEG segments. */
export function makeCurves(E, SEG = 24) {
  const g = new THREE.InstancedBufferGeometry();
  const aT = new Float32Array((SEG + 1) * 2), aSide = new Float32Array((SEG + 1) * 2);
  const pos = new Float32Array((SEG + 1) * 2 * 3);             // dummy (three wants "position")
  const idx = [];
  for (let i = 0; i <= SEG; i++) {
    aT[i * 2] = aT[i * 2 + 1] = i / SEG; aSide[i * 2] = -1; aSide[i * 2 + 1] = 1;
    if (i < SEG) { const a = i * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
  }
  g.setIndex(idx);
  g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  g.setAttribute("aT", new THREE.BufferAttribute(aT, 1));
  g.setAttribute("aSide", new THREE.BufferAttribute(aSide, 1));
  const inst = (n) => { const a = new THREE.InstancedBufferAttribute(new Float32Array(E * n), n); a.setUsage(THREE.DynamicDrawUsage); return a; };
  const aP0 = inst(3), aP1 = inst(3), aC = inst(3), aMeta = inst(4);   // meta: alpha, delay 0..1, widthScale, seed
  g.setAttribute("aP0", aP0); g.setAttribute("aP1", aP1); g.setAttribute("aC", aC); g.setAttribute("aMeta", aMeta);
  g.instanceCount = E;

  const mat = new THREE.ShaderMaterial({
    uniforms: {
      uRes: { value: new THREE.Vector2(1, 1) },   // DRAWING-BUFFER pixels: renderer.getDrawingBufferSize()
      uWidth: { value: 1.0 },                     // line width in device pixels (1.0 = hairline)
      uColor: { value: new THREE.Color("#ffffff") }, uOpacity: { value: 0.5 },
      uFade: { value: new THREE.Vector2(8, 60) },  // view-space depth where alpha goes 1 -> 0
      uProgress: { value: 1 },                    // 0..1 draw-on reveal (with per-instance delay)
    },
    vertexShader: /* glsl */ `
      attribute float aT; attribute float aSide; attribute vec3 aP0; attribute vec3 aP1; attribute vec3 aC; attribute vec4 aMeta;
      uniform vec2 uRes; uniform float uWidth; uniform vec2 uFade; uniform float uProgress;
      varying float vDist; varying float vA; varying float vT; varying float vReveal; varying float vHalf;
      void main() {
        float t = aT, u = 1. - t;
        vec3 p  = u*u*aP0 + 2.*u*t*aC + t*t*aP1;
        vec3 dp = 2.*u*(aC - aP0) + 2.*t*(aP1 - aC);
        vec4 vp = viewMatrix * vec4(p, 1.);
        vec4 c0 = projectionMatrix * vp;
        vec4 c1 = projectionMatrix * viewMatrix * vec4(p + dp * .02, 1.);
        vec2 d = (c1.xy / c1.w - c0.xy / c0.w) * uRes * .5;               // tangent in pixels
        d = d / max(length(d), 1e-5);
        vec2 n = vec2(-d.y, d.x);
        float half_ = max(uWidth * aMeta.z, .5) * .5 + 1.0;               // +1 px AA skirt
        c0.xy += n * aSide * half_ * 2. / uRes * c0.w;
        gl_Position = c0;
        vDist = aSide * half_; vHalf = uWidth * aMeta.z * .5;
        float depth = -vp.z;
        vA = aMeta.x * (1. - smoothstep(uFade.x, uFade.y, depth)) * step(.05, c0.w);
        vT = t; vReveal = clamp((uProgress - aMeta.y * .6) / .4, 0., 1.) * 1.07;
      }`,
    fragmentShader: /* glsl */ `
      uniform vec3 uColor; uniform float uOpacity;
      varying float vDist; varying float vA; varying float vT; varying float vReveal; varying float vHalf;
      void main() {
        float cov = clamp(vHalf - abs(vDist) + .5, 0., 1.);                // 1px-wide AA ramp, exact for sub-pixel widths
        float head = 1. - smoothstep(vReveal - .07, vReveal, vT);
        float a = cov * vA * uOpacity * head;
        if (a < .002) discard;
        gl_FragColor = vec4(uColor, a);
        #include <colorspace_fragment>
      }`,
    transparent: true, depthWrite: false, depthTest: true, side: THREE.DoubleSide,
  });
  const mesh = new THREE.Mesh(g, mat); mesh.frustumCulled = false; mesh.renderOrder = 1;
  return { mesh, mat, aP0, aP1, aC, aMeta };
}
```
Usage: `const c = makeCurves(E); scene.add(c.mesh); c.aP0.array.set(...); c.aP0.needsUpdate = true; c.mat.uniforms.uRes.value.copy(drawingBufferSize);`
Control point recipe for a nice arc between a and b: `ctrl = (a+b)/2 + normalize(cross(b−a, up))·|b−a|·0.2·(±1) + up·|b−a|·0.08`.
Lines that must follow posters mid-morph: update `aP0/aP1` from `posters.current(i)` each frame for the few edges involved (focus mode: 5 edges), or put the edge endpoints through the same morph attributes.

---------------------------------------------------------------------------------------------

## 4. Text in 3D

- **Tiny mono uppercase labels (the common case): use the DOM, not troika.** Project the anchor with
  `v.copy(p).project(camera)` → `el.style.transform = translate3d(x,y,0)`; pixel-perfect Geist Mono at any dpr, free letter-spacing/`mix-blend-mode`,
  zero draw calls. This is what Active Theory/Abeto do for HUD-scale type. Hide with `opacity`/`visibility` (not display) to avoid layout; reuse a pool.
  Put labels in `ctx.overlay` (already cleared on mode exit). Do the projection in `update()` only for visible ones (≤ ~80).
- **Big in-world titles (serif, rings of text, tunnel words): troika-three-text** [tested compiles]. One `Text` = one mesh/draw call + a worker for layout,
  so keep it ≲ 60 alive. SDF glyph atlas shared across instances of the same font.
- troika **cannot read woff2** (Google Fonts CSS serves woff2). Use woff/ttf files: [verified 200 OK]
  `https://cdn.jsdelivr.net/npm/@fontsource/geist-mono/files/geist-mono-latin-400-normal.woff`,
  `.../@fontsource/instrument-serif/files/instrument-serif-latin-400-normal.woff` (and `-italic.woff`). Better: curl them into `public/fonts/` so it works offline (`/fonts/…`).
- No TS types ship for troika here: add `declare module "troika-three-text";` in `src/troika.d.ts` (engine agent) or `// @ts-expect-error`.

```ts
import { Text, preloadFont } from "troika-three-text";
preloadFont({ font: "/fonts/geist-mono-400.woff", characters: "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ·—" }, () => {});   // before first frame → no pop
const t = new Text();
Object.assign(t, { text: "ORBIT", font: "/fonts/geist-mono-400.woff", fontSize: 0.06, letterSpacing: 0.12 /* em */,
  anchorX: "left", anchorY: "middle", color: 0xffffff, fillOpacity: 0.7, sdfGlyphSize: 64 });
(t.material as THREE.Material).depthWrite = false; (t.material as THREE.Material).transparent = true;
t.sync();                                   // lays out in a worker; mesh appears when ready
group.add(t);
// billboard (per frame, few labels):  t.quaternion.copy(camera.quaternion);
// crisp tiny text: fontSize so glyph x-height ≥ ~9 device px at its typical distance; raise sdfGlyphSize to 96 for serif; don't rotate tiny text off-axis.
// fade with depth: t.fillOpacity = base * (1 - smoothstep(near, far, dist)); dispose: t.dispose(); group.remove(t)
```
Render troika text AFTER opaque posters; set `t.renderOrder = 2`. If it appears blurry inside the composer, it is MSAA/DoF — put DoF's `focusRange` wide enough to cover text, or fade it in only near the focus plane.

---------------------------------------------------------------------------------------------

## 5. Post-processing — pmndrs `postprocessing` 6.39.6 with three r187 [tested: runs, no GL errors]

```ts
import { EffectComposer, EffectPass, RenderPass, DepthOfFieldEffect, BloomEffect, ChromaticAberrationEffect,
         VignetteEffect, NoiseEffect, BlendFunction } from "postprocessing";

// renderer: new THREE.WebGLRenderer({ antialias: false, powerPreference: "high-performance" })   // MSAA lives in the composer instead
const composer = new EffectComposer(renderer, { frameBufferType: THREE.HalfFloatType, multisampling: 4, depthBuffer: true });
composer.addPass(new RenderPass(scene, camera));
const dof   = new DepthOfFieldEffect(camera, { focusDistance: 10, focusRange: 6, bokehScale: 2, resolutionScale: 0.5 });  // world units; deprecated: worldFocus*, focalLength
const bloom = new BloomEffect({ intensity: 0.6, luminanceThreshold: 1.0, luminanceSmoothing: 0.15, mipmapBlur: true });
const ca    = new ChromaticAberrationEffect({ offset: new THREE.Vector2(0.0006, 0.0006), radialModulation: true, modulationOffset: 0.35 });
const vig   = new VignetteEffect({ offset: 0.3, darkness: 0.55 });
const grain = new NoiseEffect({ blendFunction: BlendFunction.SOFT_LIGHT, premultiply: true }); grain.blendMode.opacity.value = 0.3;
composer.addPass(new EffectPass(camera, dof, bloom, ca, vig, grain));   // ONE merged pass → one extra fullscreen draw
// loop:  composer.render(dt)      // replaces renderer.render
// resize: composer.setSize(w, h)  // (and renderer.setPixelRatio first)
```
- **Colour space:** HalfFloat buffer = linear HDR; the last EffectPass applies the sRGB encode (we output linear with `colorspace_fragment`; no `ToneMappingEffect`, posters must keep their colours).
  Set `scene.background`, not `setClearColor` (§0.1).
- **Keep posters crisp:** (a) bloom `luminanceThreshold: 1.0` — posters never exceed 1.0, only HDR lights/lines (colour×2–3) bloom; (b) DoF `target`/`focusDistance` tracks the focused poster
  (`dof.target = focusedWorldPos` — the effect keeps `focusDistance` from it each frame) with a generous `focusRange` (≈ 0.6×distance to the focus) so the whole readable cluster stays sharp and only far/near
  stuff blurs; ramp `bokehScale` 0 → 2 on focus, and `dofPass.enabled=false` (own EffectPass) when ≈0 to save ~1 ms; (c) CA offset ≤ 0.0008 with `radialModulation` = edges only; (d) MSAA 4 gives hard-edge AA; no FXAA/SMAA on top (they soften textures).
- **Grain:** `NoiseEffect` + SOFT_LIGHT @ 0.2–0.35 is the cheapest convincing film grain (one rand per pixel, merged into the same pass, ≈0.05 ms). It re-seeds every frame (time-driven). The poster dither (§1) hides inside it.
- **SelectiveBloom:** not needed — HDR threshold trick above does it with no extra scene render. (`SelectiveBloomEffect` re-renders selected objects; reserve it for light theme if bloom must hit specific objects.)
- **Cost (M1 Pro, 1440×900 @ dpr2 ≈ 2880×1800):** MSAA4 HalfFloat input buffer ≈ 165 MB, + DoF ≈ 1–1.5 ms (half-res), bloom mipmapBlur ≈ 0.6 ms, rest ≈ 0.3 ms → ~2–3 ms. 120 fps = 8.3 ms budget: OK only with the §8 adaptive dpr (cap composer dpr at 1.5 while in motion on 16" displays).
- `NoiseEffect` default blend is SCREEN-ish; SOFT_LIGHT needs `premultiply:true` to avoid lifting black. Check the black floor by screenshot (5,5,5 must stay ≈ 5).
- DoF + MSAA: if you ever see depth artifacts at poster edges, drop `multisampling` to 0 and add `SMAAEffect` (still merged), or keep MSAA and set DoF `resolutionScale` 0.5.
- Light theme (BLOOM mode): bloom intensity → 0 (don't glow ink); keep grain (paper tooth) at 0.15 with `BlendFunction.MULTIPLY`-ish look via SOFT_LIGHT; vignette `darkness 0.25`.

---------------------------------------------------------------------------------------------

## 6. Camera — buttery

Current rig: `lerp(desired, 1 − (1−0.12)^(dt·60))` per frame (frame-rate independent: good) fed by a gsap tween that lerps position & lookAt **linearly**.
Upgrades, in order of visible payoff:
1. **Curved fly-to with a dolly-zoom punch**, not a straight line: Bézier position path, lookAt eased slightly *ahead*, and fov breathes +5° mid-flight (the "whoosh" every Lusion/AT site has).
2. **Exponential damping helper** everywhere (`damp(a,b,λ,dt)`): λ≈8 for wheel zoom, ≈5 orbit, ≈3 parallax.
3. **Drag inertia**: store pointer velocity (EMA over last ~80 ms), on release continue `vel *= exp(−4 dt)` — that is the whole "weight" feeling.
4. **Pointer parallax**: offset the desired position along camera right/up by `ndc × 0.15` (damped λ≈3); the world is never still.
5. **Wheel**: normalise `deltaMode` and trackpad pinch (`e.ctrlKey` → ×10 sensitivity); zoom in *log-space* (`logR += dy*0.0012`), `r = exp(damp(logR…))` → equal feel at any distance.
6. **Touch/pinch**: Pointer Events, `Map<pointerId, {x,y}>`; 1 pointer = orbit, 2 = pinch (distance ratio → logR) + pan (midpoint delta). Set `touch-action:none` on the canvas.
7. Keep two stages of smoothing (gsap shaping + rig damping) but then use a *short* damping (0.18–0.25) for fly-to so it doesn't lag a full second behind the tween.

```ts
export const damp = (a: number, b: number, lambda: number, dt: number) => a + (b - a) * (1 - Math.exp(-lambda * dt));

export function curvedFly(rig: CameraRig, to: THREE.Vector3, look: THREE.Vector3, dur = 1.6, lift = 0.18, fovPunch = 5) {
  const p0 = rig.position.clone(), l0 = rig.lookAt.clone(), L = p0.distanceTo(to);
  const side = new THREE.Vector3().subVectors(to, p0).cross(new THREE.Vector3(0.2, 1, 0.1)).normalize();
  const c = p0.clone().add(to).multiplyScalar(0.5).addScaledVector(side, L * lift).addScaledVector(new THREE.Vector3(0, 1, 0), L * lift * 0.4);
  const fov0 = rig.camera.fov, s = { t: 0 }, a = new THREE.Vector3(), b = new THREE.Vector3();
  return new Promise<void>((res) => gsap.to(s, { t: 1, duration: dur, ease: "expo.inOut", onComplete: res, onUpdate() {
    const t = s.t, u = 1 - t;
    rig.position.copy(a.copy(p0).multiplyScalar(u * u)).addScaledVector(c, 2 * u * t).addScaledVector(to, t * t);   // quadratic Bézier
    rig.lookAt.lerpVectors(l0, look, Math.min(1, t * 1.15));                                                     // look leads the move
    rig.camera.fov = fov0 + fovPunch * Math.sin(Math.PI * t); rig.camera.updateProjectionMatrix();
  } }));
}
// drag inertia (inside a mode): onMove: vel = vel*0.7 + (dx,dy)/dt*0.3;  onUp: coast = true;  update: if (coast) { yaw += vel.x*dt; vel.multiplyScalar(Math.exp(-4*dt)); }
```
(Note `rig.position`/`lookAt` are the *desired* vectors; the engine's damping lerps the real camera toward them. Don't also lerp inside the tween callback — assign.)
Use `renderer.setAnimationLoop` as today: on a ProMotion Mac Chrome/Safari run rAF at 120 Hz. Cap `dt` (already 0.05). Avoid `camera.lookAt` roll drift: keep `camera.up = (0,1,0)` except in TUNNEL.

---------------------------------------------------------------------------------------------

## 7. Picking at 120 fps — GPU ID picking [tested: returns the right instance id]

CPU `InstancedMesh.raycast` = 990 matrix tests + a ray/plane each, per pointermove (~0.3–1 ms), and it reads `instanceMatrix`, which no longer exists once morphing is on the GPU (§2). GPU picking renders the posters once with
an ID shader into a **1×1 render target** (via `camera.setViewOffset` so only the pixel under the cursor is rasterised) — cost is the vertex stage only (~12k tris) ≈ 0.05 ms — and reads it back with
`renderer.readRenderTargetPixelsAsync` (non-blocking, exists in r187). Hover latency is 1–2 frames, invisible at 120 Hz.

```ts
// poster material variants: pickMat = posterMat.clone(); pickMat.defines = { PICK: "" }; pickMat.alphaToCoverage = false;   // FS writes (id+1) into rgb, discards opacity<.5
const PICK_LAYER = 1; mesh.layers.enable(PICK_LAYER);                 // posters on layers 0 and 1; everything else only layer 0
const rt = new THREE.WebGLRenderTarget(1, 1, { depthBuffer: true });   // UnsignedByte RGBA, no colour management
const px = new Uint8Array(4); let busy = false;
export async function gpuPick(x: number, y: number): Promise<number | null> {    // x,y CSS pixels
  if (busy) return null; busy = true;
  const bg = scene.background; scene.background = null; const saveMask = camera.layers.mask;
  camera.setViewOffset(innerWidth, innerHeight, x | 0, y | 0, 1, 1); camera.layers.set(PICK_LAYER);
  scene.overrideMaterial = pickMat; renderer.setRenderTarget(rt);
  const ca = renderer.getClearAlpha(); renderer.setClearAlpha(0); renderer.clear(); renderer.render(scene, camera);
  renderer.setRenderTarget(null); renderer.setClearAlpha(ca);
  scene.overrideMaterial = null; scene.background = bg; camera.clearViewOffset(); camera.layers.mask = saveMask;
  await renderer.readRenderTargetPixelsAsync(rt, 0, 0, 1, 1, px); busy = false;
  const id = px[0] | (px[1] << 8) | (px[2] << 16); return id ? id - 1 : null;
}
```
- Call it at most once per animation frame from `pointermove` (store the latest x,y; do the pick at the start of the next frame). Ghost posters (≤48, real Meshes) keep using the CPU raycaster *only when GPU pick returns null*.
- Pick uses the poster's *current* animated pose (same VS) so hovering a flying poster works; opacity < 0.5 posters are skipped like `target.opacity > 0.05` today.
- `scene.overrideMaterial` + layers ensures lines/text/particles never enter the pick pass.

---------------------------------------------------------------------------------------------

## 8. Performance

- **Draw calls target ≤ 12**: posters 1, lines 1–2, particles 1, troika ≤ 30 labels, composer ≈ 8 fullscreen passes. Check `renderer.info.render.calls` / `.triangles` (reset happens per `render`; with a composer it sums passes — read it
  right after `composer.render`, set `renderer.info.autoReset = false` and reset manually once per frame if you want per-frame totals).
- **No per-frame allocation**: hoist every `Vector3/Quaternion/Matrix4`, no `.map/.filter/{...}` in `update`; the poster field's `pending.filter` allocates every frame (CPU morph code) → gone with §2. Typed arrays only.
- **dpr**: `renderer.setPixelRatio(Math.min(devicePixelRatio, 2))` max; start at 1.5 on ≥ 2.0 displays with a composer. **Adaptive quality:**

```ts
// call once per frame with the frame's ms; steps dpr down when slower than ~8.8 ms (113 fps), up when idle headroom
let acc = 0, frames = 0, dpr = Math.min(devicePixelRatio, 1.75);
const LADDER = [2, 1.75, 1.5, 1.25, 1];
export function adapt(ms: number) {
  acc += ms; if (++frames < 90) return;
  const avg = acc / frames; acc = 0; frames = 0;
  let i = LADDER.findIndex((d) => d <= dpr + 1e-3); if (i < 0) i = LADDER.length - 1;
  if (avg > 8.8 && i < LADDER.length - 1) i++;                          // slower than 113 fps → drop
  else if (avg < 6.2 && i > 0 && LADDER[i - 1] <= devicePixelRatio) i--;  // lots of headroom → raise
  const next = LADDER[i]; if (next !== dpr) { dpr = next; renderer.setPixelRatio(dpr); composer.setSize(innerWidth, innerHeight); lineMat.uniforms.uRes.value.copy(renderer.getDrawingBufferSize(v2)); }
}
```
  (rAF delivers 60 Hz when the tab is throttled / on 60 Hz displays: then avg ≈ 16.6 → ladder would run down. Cap the target with a measured refresh: `target = min(8.8, 1.06 * medianRafInterval)`.)
- **Shader compile hitches:** `await renderer.compileAsync(scene, camera)` (exists in r187, uses KHR_parallel_shader_compile) after building a mode's objects and BEFORE revealing — otherwise the first frame of a new mode stalls 50–300 ms (composer effects too: render the composer once offscreen at startup).
- **Texture upload hitches:** big `DataArrayTexture` uploads block the frame; do them during the intro, in 2–3 chunks, `await new Promise(requestAnimationFrame)` between.
- Pointer events: handle in the event, but only *apply* in the rAF (store x,y). Avoid `getBoundingClientRect` per move (cache on resize).
- DOM labels: update `transform` only (compositor-only), `will-change: transform`; skip off-screen/behind-camera ones.
- `frustumCulled = false` on instanced stuff (bounding sphere is wrong); but cull your own *lines* per mode if a mode has > 20k segments.
- Memory: composer HalfFloat MSAA4 at 3456×2234 ≈ 250 MB; watch `renderer.info.memory.textures`. Dispose mode-created geometries/materials/Text in `exit`.

---------------------------------------------------------------------------------------------

## 9. Wow list we can afford tonight (ranked by look ÷ effort)

1. **Dust / star particles (GPU only, 1 draw call)** — 6–20k `THREE.Points`, all motion in the VS (zero CPU), soft round sprites with depth fade and slight twinkle; gives parallax depth in every mode.
```glsl
// VS: attribute vec4 aSeed (xyz = random in [0,1)^3, w = size rand); uniforms uTime, uBox (vec3), uCenter (vec3), uSize, uDpr
vec3 p = fract(aSeed.xyz + vec3(.004, .007, .003) * uTime * (.5 + aSeed.w));         // wraps inside the box forever
vec3 w = (p - .5) * uBox + uCenter;                                                  // uCenter = camera target → dust follows you
vec4 mv = viewMatrix * vec4(w, 1.); gl_Position = projectionMatrix * mv;
gl_PointSize = uSize * uDpr * (.6 + aSeed.w) * (1. / -mv.z);                         // perspective size; clamp to ≥1.2 px
vAlpha = (1. - smoothstep(10., 40., -mv.z)) * (.4 + .6 * sin(uTime * 2. + aSeed.w * 40.) * .5 + .3);
// FS: float d = length(gl_PointCoord - .5); gl_FragColor = vec4(uColor, vAlpha * smoothstep(.5, .1, d)); #include <colorspace_fragment>
// material: transparent, depthWrite:false, blending: AdditiveBlending (dark theme); renderOrder 1; frustumCulled=false
```
2. **Idle breathing + hover lean** — already in the poster VS (`sway`, `uTilt`). Costs nothing; this alone separates "alive" from "dev".
3. **Rim/edge light + sheen** (in the FS) — physical presence for free.
4. **Focus choreography**: on focus, `posters.dim(0.75, [id, ...next])` + DoF ramp (`bokehScale 0→2`, 0.8 s) + bloom intensity up 20% + curved fly with fov punch + lines draw-on (`uProgress` 0→1 over 0.9 s, per-edge delay) — one gsap timeline.
5. **Fake per-instance DoF** (cheaper than DoF): in the poster FS, `lod = clamp(abs(viewDepth − uFocus) * k, 0, 4)`, then `textureLod(uTex, uvw, baseLod + lod)` → far/near posters get blurrier through *mip bias*, near-free. Combine with real DoF only on the focus mode. (Needs view depth varying: `vDepth = -(viewMatrix*vec4(world,1)).z`.)
6. **Shockwave on mode switch**: a ring SDF in the poster FS driven by `uTime − uWaveT0` brightening posters as the wavefront crosses their world radius — "the world ripples as it reforms". 10 lines of shader.
7. **Opacity "stagger-in" at load**: opacity from the intro as `aToS.y` with morph delay by distance from centre → they bloom outward from the middle; matches the `intro` expectation.
8. **Sorted transparency**: avoid. Ghosts (≤48) can stay `transparent:true, depthWrite:false`, sorted by three per object (they are separate Meshes).
9. **Reflective floor / shadows**: skip tonight (a blurred mirrored copy needs a second poster draw; a baked radial "contact glow" sprite under hovered posters is cheaper).
10. **Subtle luminance-based chroma on the edge of focus**: ChromaticAberration `modulationOffset` already does it; keep the offset tiny.

---------------------------------------------------------------------------------------------

## 10. Integration order for the engine agent (what to do first)

1. `scene.background` instead of `setClearColor` (§0.1) — 2-line fix, immediate visual win.
2. Composer + effects (§5) with `colorspace_fragment` in poster FS and `colorSpace = SRGBColorSpace` on the texture (§0.2). Verify black = 5,5,5.
3. Per-poster layers (§1) → crisp, bleed-free, 40% less memory than today's 439 MB.
4. GPU morph + poster material (§1–2) → buttery transitions, 0 ms CPU; then GPU picking (§7) — they must land together.
5. Lines (§3) and dust (§9.1) for modes; DOM labels (§4).
6. Camera upgrades (§6), adaptive dpr (§8), compileAsync (§8).

## 11. Open questions / not verified
- Real fps of the full pipeline on the 16" at dpr 2 — measure with `tools/capture.mjs` once §5 is in; the budget numbers above are estimates from similar scenes.
- KTX2 encoding pipeline (`ktx2-encoder`) not tried. Safari WebGL2 `alphaToCoverage` with composer MSAA not tried (Chrome/ANGLE-Metal only).
- Why `renderer.setClearColor` double-encodes in this Chrome/three combination wasn't root-caused; `scene.background` is the verified workaround.
