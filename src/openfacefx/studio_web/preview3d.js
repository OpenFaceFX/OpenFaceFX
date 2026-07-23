/* ===================================================================== *
 *  OpenFaceFX Studio — 3D preview (three.js)
 *  Loads an ARKit-blendshape head (facecap.glb) and drives its morph
 *  targets from the take, retargeted to ARKit — a real driven face, like
 *  the reference tool. Falls back silently to the schematic SVG if WebGL
 *  or the CDN modules aren't available (studio.js handles the fallback).
 * ===================================================================== */
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { KTX2Loader } from "three/addons/loaders/KTX2Loader.js";
import { MeshoptDecoder } from "three/addons/libs/meshopt_decoder.module.js";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const CDN_BASIS = "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/libs/basis/";
const MODEL = "assets/facecap.glb";

const P = {
  ready: false, active: false,
  renderer: null, scene: null, camera: null, controls: null,
  meshes: [], head: null, morphs: {}, pose: { pitch: 0, yaw: 0, roll: 0 },
};

// studio arkit channel name -> facecap morph name (Left/Right -> _L/_R)
const arkitToModel = n => n.replace(/Left$/, "_L").replace(/Right$/, "_R");

async function init() {
  const canvas = document.getElementById("face3d");
  if (!canvas) return;
  const gl = canvas.getContext("webgl2") || canvas.getContext("webgl");
  if (!gl) return;                                   // no WebGL -> keep SVG

  try {
    P.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    P.renderer.setPixelRatio(Math.min(2, devicePixelRatio || 1));
    P.scene = new THREE.Scene();
    P.camera = new THREE.PerspectiveCamera(26, 1, 0.01, 100);

    P.scene.add(new THREE.AmbientLight(0xffffff, 1.15));
    const key = new THREE.DirectionalLight(0xfff1d8, 2.1); key.position.set(0.6, 1.0, 1.4); P.scene.add(key);
    const rim = new THREE.DirectionalLight(0x88b6ff, 0.8); rim.position.set(-1.2, 0.4, -0.8); P.scene.add(rim);

    P.controls = new OrbitControls(P.camera, canvas);
    P.controls.enablePan = true; P.controls.enableZoom = true;
    P.controls.enableDamping = true; P.controls.dampingFactor = 0.08;
    P.controls.rotateSpeed = 0.6;
    // real zoom range is set model-relative in frame(); these are placeholders
    P.controls.minDistance = 0.05; P.controls.maxDistance = 100;

    const ktx2 = new KTX2Loader().setTranscoderPath(CDN_BASIS).detectSupport(P.renderer);
    const loader = new GLTFLoader().setKTX2Loader(ktx2).setMeshoptDecoder(MeshoptDecoder);
    const gltf = await loader.loadAsync(MODEL);

    P.head = gltf.scene;
    P.head.traverse(o => { if (o.isMesh && o.morphTargetDictionary) P.meshes.push(o); });
    if (!P.meshes.length) return;                    // unexpected model -> keep SVG
    P.scene.add(P.head);
    frame();
    P.ready = true;
    window.dispatchEvent(new Event("preview3d-ready"));
    loop();
    addEventListener("resize", resize);
  } catch (e) {
    // any failure (offline CDN, decoder, WebGL) -> silent fallback to the SVG
    P.ready = false;
  }
}

function frame() {
  resize();                                          // set aspect from the live canvas first
  const box = new THREE.Box3().setFromObject(P.head);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z) || 0.3;
  // Fit the WHOLE head in the (now full-stage) canvas with a little margin, and
  // frame it front-on centred slightly above middle (eyes/nose). Front is +Z.
  // Distance derived from the vertical FOV so the head fills the frame instead
  // of floating mid-screen, and never gets cut off.
  const vfov = P.camera.fov * Math.PI / 180;
  const fitH = (size.y * 1.25) / (2 * Math.tan(vfov / 2));        // fit height + 25% margin
  const fitW = (size.x * 1.25) / (2 * Math.tan(vfov / 2) * Math.max(0.75, P.camera.aspect || 1));
  const dist = Math.max(fitH, fitW, maxDim * 1.6);
  const faceY = center.y + size.y * 0.04;                         // a touch above centre
  P.camera.near = Math.max(0.001, maxDim * 0.01);
  P.camera.far = maxDim * 200; P.camera.updateProjectionMatrix();
  P.camera.position.set(center.x, faceY, center.z + dist);
  P.controls.target.set(center.x, faceY, center.z);
  P.controls.minDistance = maxDim * 0.35;   // zoom right in to the lips…
  P.controls.maxDistance = maxDim * 60;      // …or way out — no early clamp
  P.home = { pos: P.camera.position.clone(), target: P.controls.target.clone() };
  P.controls.update();
  resize();
}
// public reset so the UI can recentre the head
function reframe() { if (P.head) frame(); }

function resize() {
  if (!P.renderer) return;
  const c = P.renderer.domElement, w = c.clientWidth || 1, h = c.clientHeight || 1;
  P.renderer.setSize(w, h, false);
  P.camera.aspect = w / h; P.camera.updateProjectionMatrix();
}

function applyMorphs() {
  for (const m of P.meshes) {
    const d = m.morphTargetDictionary, inf = m.morphTargetInfluences;
    for (let i = 0; i < inf.length; i++) inf[i] = 0;         // reset
    for (const [name, v] of Object.entries(P.morphs)) {
      const idx = d[name]; if (idx !== undefined) inf[idx] = v;
    }
  }
  if (P.head) {                                    // animation head pose (small angles)
    P.head.rotation.set(P.pose.pitch, P.pose.yaw, P.pose.roll);
  }
}

function loop() {
  if (!P.ready) return;
  applyMorphs();
  P.controls.update();
  P.renderer.render(P.scene, P.camera);
  requestAnimationFrame(loop);
}

/* public: studio.js pushes the frame's values here.
 * arkit = {arkitTargetName: value}, gestures = {blink_L,blink_R,browUp,...},
 * pose = {pitch,yaw,roll} radians. */
// dolly the camera along its view axis (factor<1 zooms in, >1 out), clamped
function zoom(factor) { if (!P.camera || !P.controls) return;
  const t = P.controls.target, dir = P.camera.position.clone().sub(t);
  const d = Math.max(P.controls.minDistance, Math.min(P.controls.maxDistance, dir.length() * factor));
  P.camera.position.copy(t).add(dir.setLength(d)); P.controls.update();
}
function update(arkit, gestures, pose) {
  const m = {};
  for (const [n, v] of Object.entries(arkit || {})) m[arkitToModel(n)] = Math.min(1, Math.max(0, v));
  const g = gestures || {};
  const set = (name, v) => { if (v > 0) m[name] = Math.min(1, Math.max(m[name] || 0, v)); };
  set("eyeBlink_L", Math.max(g.blink_L || 0, g.blink || 0));
  set("eyeBlink_R", Math.max(g.blink_R || 0, g.blink || 0));
  const brow = Math.max(g.browUp || 0, g.browInnerUp || 0);
  set("browInnerUp", brow); set("browOuterUp_L", (g.browOuterUp || brow)); set("browOuterUp_R", (g.browOuterUp || brow));
  P.morphs = m;
  if (pose) P.pose = pose;
}

function setActive(on) { P.active = on; if (on) resize(); }

window.Preview3D = { get ready() { return P.ready; }, update, setActive, resize, reframe, zoom };
init();
