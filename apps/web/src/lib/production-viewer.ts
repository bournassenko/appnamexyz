import * as THREE from 'three';
import type { ProductionScene } from './production';

export function createViewer(host: HTMLElement, data: ProductionScene) {
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x808080, 0.08);
  const canvas = renderer.domElement;
  canvas.setAttribute('aria-label', '3D tactile map. Drag to rotate, pinch or scroll to zoom. Use arrow keys to rotate, plus or minus to zoom, Home to reset.');
  canvas.tabIndex = 0;
  canvas.style.cssText = 'display:block;width:100%;height:100%;touch-action:none;border-radius:8px';
  host.appendChild(canvas);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 20000);
  camera.up.set(0, 0, 1);
  const group = new THREE.Group();
  scene.add(group);
  scene.add(new THREE.AmbientLight(0xffffff, 1.1));
  const light = new THREE.DirectionalLight(0xffffff, 3);
  light.position.set(-200, -200, 150);
  scene.add(light);
  const material = new THREE.MeshStandardMaterial({ color: data.mode === 'print' ? 0xb8b8b8 : 0xf2eee6, roughness: 0.88, metalness: 0 });
  const width = data.width_mm, height = data.height_mm;
  const plate = new THREE.Mesh(new THREE.BoxGeometry(width, height, data.base_thickness_mm), material);
  plate.position.z = -data.base_thickness_mm / 2;
  group.add(plate);
  const convert = (ring: number[][]) => ring.map(point => new THREE.Vector2(point[0] - width / 2, height / 2 - point[1]));
  if (data.mode !== 'emboss') {
    const polygons = data.graphics.type === 'Polygon' ? [data.graphics.coordinates as number[][][]] : data.graphics.type === 'MultiPolygon' ? data.graphics.coordinates as number[][][][] : [];
    for (const rings of polygons) {
      const shape = new THREE.Shape(convert(rings[0]));
      for (const ring of rings.slice(1)) shape.holes.push(new THREE.Path(convert(ring)));
      const geometry = new THREE.ExtrudeGeometry(shape, { depth: data.relief_height_mm, bevelEnabled: false, steps: 1, curveSegments: 6 });
      group.add(new THREE.Mesh(geometry, material));
    }
  }
  const dotGeometry = new THREE.SphereGeometry(1, 12, 8);
  const addDots = (dots: number[][], elevation: number) => {
    if (!dots.length) return;
    const mesh = new THREE.InstancedMesh(dotGeometry, material, dots.length);
    const transform = new THREE.Object3D();
    dots.forEach(([x, y, radius], index) => {
      transform.position.set(x - width / 2, height / 2 - y, 0);
      transform.scale.set(radius, radius, elevation);
      transform.updateMatrix();
      mesh.setMatrixAt(index, transform.matrix);
    });
    group.add(mesh);
  };
  addDots(data.graphic_dots, data.relief_height_mm);
  addDots(data.braille_dots, data.braille_height_mm);
  let azimuth = 0, elevation = 0.8, distance = Math.max(width, height) * 1.7;
  const render = () => {
    camera.position.set(distance * Math.cos(elevation) * Math.sin(azimuth), -distance * Math.cos(elevation) * Math.cos(azimuth), distance * Math.sin(elevation));
    camera.lookAt(0, 0, 0);
    renderer.render(scene, camera);
  };
  const resize = () => { const box = host.getBoundingClientRect(); renderer.setSize(box.width, box.height, false); camera.aspect = box.width / Math.max(1, box.height); camera.updateProjectionMatrix(); render(); };
  const observer = new ResizeObserver(resize);
  observer.observe(host);
  const pointers = new Map<number, { x: number; y: number }>();
  let pinch = 0;
  const down = (event: PointerEvent) => { canvas.setPointerCapture(event.pointerId); pointers.set(event.pointerId, { x: event.clientX, y: event.clientY }); canvas.focus(); pinch = 0; };
  const move = (event: PointerEvent) => {
    const previous = pointers.get(event.pointerId);
    if (!previous) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 1) { azimuth -= (event.clientX - previous.x) * 0.008; elevation = Math.max(0.1, Math.min(1.55, elevation + (event.clientY - previous.y) * 0.008)); }
    else { const [a, b] = [...pointers.values()]; const length = Math.hypot(a.x - b.x, a.y - b.y); if (pinch && length) distance *= pinch / length; pinch = length; }
    distance = Math.max(20, Math.min(10000, distance)); render();
  };
  const up = (event: PointerEvent) => { pointers.delete(event.pointerId); pinch = 0; };
  const zoom = (factor: number) => { distance = Math.max(20, Math.min(10000, distance * factor)); render(); };
  const reset = () => { azimuth = 0; elevation = 0.8; distance = Math.max(width, height) * 1.7; render(); };
  const rotate = (amount: number) => { azimuth += amount; render(); };
  const wheel = (event: WheelEvent) => { event.preventDefault(); zoom(event.deltaY > 0 ? 1.1 : 0.9); };
  const key = (event: KeyboardEvent) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-', 'Home'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Home') reset();
    else if (event.key === '+' || event.key === '=') zoom(0.9);
    else if (event.key === '-') zoom(1.1);
    else if (event.key === 'ArrowLeft') rotate(-0.12);
    else if (event.key === 'ArrowRight') rotate(0.12);
    else { elevation = Math.max(0.1, Math.min(1.55, elevation + (event.key === 'ArrowUp' ? 0.1 : -0.1))); render(); }
  };
  canvas.addEventListener('pointerdown', down); canvas.addEventListener('pointermove', move); canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up); canvas.addEventListener('wheel', wheel, { passive: false }); canvas.addEventListener('keydown', key);
  resize();
  return { zoom, reset, rotate, light: (value: number) => { light.position.set(Math.cos(value) * 250, Math.sin(value) * 250, 100); render(); }, dispose: () => { observer.disconnect(); canvas.removeEventListener('pointerdown', down); canvas.removeEventListener('pointermove', move); canvas.removeEventListener('pointerup', up); canvas.removeEventListener('pointercancel', up); canvas.removeEventListener('wheel', wheel); canvas.removeEventListener('keydown', key); scene.traverse(object => { if (object instanceof THREE.Mesh) object.geometry.dispose(); }); material.dispose(); renderer.dispose(); canvas.remove(); } };
}
