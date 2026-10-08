/* =========================================================================
 * 여운 — 손짓으로 만나는 3D 전시관 (Three.js r147)
 *  - 전시관 전체 보기: 원본 3d 평면도 건물 (드래그 회전 · 휠 확대 · 전시실 클릭)
 *  - 전시실 감상: 작품마다 고정 감상 지점, 이전/다음으로 시점 이동
 *  - 조작: 버튼 · ← → 키 · 손짓(Python 서버가 보내는 NEXT/PREV 명령)
 * ========================================================================= */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);

  if (!window.THREE || !THREE.OrbitControls || !THREE.CSS2DRenderer || !window.FloorPlan) {
    $('loadingText').textContent = '3D 라이브러리(three.js)를 불러오지 못했어요. 인터넷 연결을 확인하거나 setup_assets.py를 실행해 주세요.';
    return;
  }

  const F = window.FloorPlan;
  const { P, PX, WALL_H, FLOOR_T, PLINTH } = F;
  const ART_Y = 1.75;          // 작품 중심 높이(바닥 기준, unit≈m)
  const ART_MAX_H = 1.6;       // 작품(그림 부분) 최대 높이
  const FRAME_B = 0.07;        // 액자 테두리 폭
  const FRAME_D = 0.06;        // 액자 두께
  const SCREEN_FILL = 0.52;    // 감상 지점에서 작품이 화면 높이를 채우는 비율

  /* ---------------- 상태 ---------------- */
  const S = {
    rooms: [], works: [], byId: {}, roomWorks: {}, slots: {}, overflow: [], total: 0,
    mode: 'overview', roomId: null, index: 0,
    animating: false, lastCmdId: 0, cardHidden: false,
    likes: new Set(), gState: 'off', searchTab: 'search',
  };
  try { S.likes = new Set(JSON.parse(localStorage.getItem('yeoun.likes') || '[]')); } catch (e) { /* 저장소 없음 */ }
  const saveLikes = () => { try { localStorage.setItem('yeoun.likes', JSON.stringify([...S.likes])); } catch (e) { /* 무시 */ } };

  async function api(path, body) {
    const opt = body === undefined ? {} : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    };
    const res = await fetch(path, opt);
    const j = await res.json().catch(() => ({}));
    if (!res.ok || j.ok === false) throw new Error(j.error || ('HTTP ' + res.status));
    return j;
  }

  let toastTimer = null;
  function toast(msg, ms = 2200) {
    const t = $('toast');
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove('show'), ms);
  }

  /* ---------------- 렌더러 / 씬 ---------------- */
  THREE.ColorManagement.legacyMode = false;
  const container = $('scene');
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(innerWidth, innerHeight);
  renderer.outputEncoding = THREE.sRGBEncoding;   // 작품 색을 원본 그대로 (톤 매핑 없음)
  container.appendChild(renderer.domElement);
  const MAX_ANISO = renderer.capabilities.getMaxAnisotropy();

  const labelRenderer = new THREE.CSS2DRenderer();
  labelRenderer.setSize(innerWidth, innerHeight);
  labelRenderer.domElement.className = 'label-layer';
  container.appendChild(labelRenderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0xe8e8ec, 160, 320);

  const viewAspect = () => (innerWidth > 0 && innerHeight > 0 ? innerWidth / innerHeight : 16 / 9);
  const camera = new THREE.PerspectiveCamera(40, viewAspect(), 0.1, 800);
  const look = new THREE.Vector3();

  const controls = new THREE.OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI * 0.47;
  controls.minDistance = 10;
  controls.maxDistance = 220;
  controls.autoRotateSpeed = 0.5;

  scene.add(new THREE.HemisphereLight(0xffffff, 0xd6d1c8, 0.58));
  const keyLight = new THREE.DirectionalLight(0xffffff, 0.42);
  keyLight.position.set(-30, 90, 40);
  const fillLight = new THREE.DirectionalLight(0xffffff, 0.16);
  fillLight.position.set(40, 50, -40);
  scene.add(keyLight, fillLight);
  // 지금 보고 있는 작품을 비추는 조명 1개 (작품 색에는 영향 없음 — 액자와 벽만 밝아짐)
  const spot = new THREE.SpotLight(0xfff3df, 0, 9, 0.55, 0.85, 1.2);
  scene.add(spot, spot.target);

  /* ---------------- 재질 ---------------- */
  const mat = (color, o = {}) => new THREE.MeshStandardMaterial({ color, roughness: 0.85, metalness: 0, ...o });
  const M = {
    ground: mat(0xe2e0dc), base: mat(0xd4d1cb), wall: mat(0xf2f0ec), partition: mat(0xf7f6f3),
    lobby: mat(0xf3f1ec), restroom: mat(0xe6e8ec), grass: mat(0xa9cf8f), path: mat(0xe4ddd0),
    trunk: mat(0x8a6a4f), leaf: mat(0x6f9a5b, { roughness: 0.9 }), wood: mat(0xc49a6c, { roughness: 0.7 }),
    dark: mat(0x3d4148, { roughness: 0.5 }), metal: mat(0x6b7078, { metalness: 0.4, roughness: 0.45 }),
    frame: mat(0x26262b, { roughness: 0.55 }), fixture: mat(0x2f2f35, { roughness: 0.4, metalness: 0.3 }),
    glass: new THREE.MeshPhysicalMaterial({ color: 0xd9e8f5, transparent: true, opacity: 0.22, roughness: 0.05, depthWrite: false }),
  };
  const edgeMat = new THREE.LineBasicMaterial({ color: 0x8c8790, transparent: true, opacity: 0.5 });

  const wallGroup = new THREE.Group();
  wallGroup.position.y = FLOOR_T;
  scene.add(wallGroup);
  const artGroup = new THREE.Group();
  scene.add(artGroup);
  const pickTargets = [];

  /* ---------------- 건물 만들기 (원본 3d/main.js 구조) ---------------- */
  function trace(target, pts) {
    pts.forEach(([x, y], i) => {
      const [wx, wz] = P(x, y);
      if (i) target.lineTo(wx, -wz); else target.moveTo(wx, -wz);
    });
    return target;
  }
  function addEdges(mesh, parent) {
    const e = new THREE.LineSegments(new THREE.EdgesGeometry(mesh.geometry, 30), edgeMat);
    e.position.copy(mesh.position);
    e.rotation.copy(mesh.rotation);
    parent.add(e);
  }
  function slab(pts, { depth, y = 0, material, edges = true, parent = scene }) {
    const geo = new THREE.ExtrudeGeometry(trace(new THREE.Shape(), pts), { depth, bevelEnabled: false, curveSegments: 1 });
    geo.rotateX(-Math.PI / 2);
    const mesh = new THREE.Mesh(geo, material);
    mesh.position.y = y;
    parent.add(mesh);
    if (edges) addEdges(mesh, parent);
    return mesh;
  }
  function block(x0, y0, x1, y1, h, material, { y = 0, parent = wallGroup, edges = true } = {}) {
    const [ax, az] = P(x0, y0), [bx, bz] = P(x1, y1);
    const w = bx - ax, d = bz - az;
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), material);
    mesh.position.set(ax + w / 2, y + h / 2, az + d / 2);
    parent.add(mesh);
    if (edges) addEdges(mesh, parent);
    return mesh;
  }
  function segmentBox(a, b, h, t, material, y, parent) {
    const [x1, z1] = P(...a), [x2, z2] = P(...b);
    const len = Math.hypot(x2 - x1, z2 - z1);
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(len, h, t), material);
    mesh.position.set((x1 + x2) / 2, y + h / 2, (z1 + z2) / 2);
    mesh.rotation.y = -Math.atan2(z2 - z1, x2 - x1);
    parent.add(mesh);
    return mesh;
  }
  function glassWall(w) {
    const FR = 0.14, POST = 1.2;
    segmentBox(w.a, w.b, FR, w.t, M.metal, 0, wallGroup);
    segmentBox(w.a, w.b, FR, w.t, M.metal, WALL_H - FR, wallGroup);
    const len = Math.hypot(w.b[0] - w.a[0], w.b[1] - w.a[1]);
    const u = [(w.b[0] - w.a[0]) / len, (w.b[1] - w.a[1]) / len];
    const bays = Math.max(1, Math.round(len / 40));
    const posts = [];
    for (let i = 1; i < bays; i++) posts.push((len * i) / bays);
    posts.forEach((s) => segmentBox(F.add(w.a, u, s - POST / 2), F.add(w.a, u, s + POST / 2), WALL_H - 2 * FR, w.t * 0.8, M.metal, FR, wallGroup));
    const stops = [0, ...posts.flatMap((s) => [s - POST / 2, s + POST / 2]), len];
    for (let i = 0; i < stops.length; i += 2) {
      segmentBox(F.add(w.a, u, stops[i]), F.add(w.a, u, stops[i + 1]), WALL_H - 2 * FR, 0.06, M.glass, FR, wallGroup);
    }
  }
  function hatchTexture(rx, ry) {
    const c = document.createElement('canvas');
    c.width = c.height = 64;
    const g = c.getContext('2d');
    g.fillStyle = '#cfcfcf'; g.fillRect(0, 0, 64, 64);
    g.strokeStyle = '#8b8b8b'; g.lineWidth = 3;
    for (let i = -64; i <= 64; i += 16) { g.beginPath(); g.moveTo(i, 64); g.lineTo(i + 64, 0); g.stroke(); }
    const t = new THREE.CanvasTexture(c);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.repeat.set(rx, ry);
    t.encoding = THREE.sRGBEncoding;
    return t;
  }
  function tree(x, y) {
    const [wx, wz] = P(x, y);
    const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.18, 0.25, 1.3, 10), M.trunk);
    trunk.position.set(wx, FLOOR_T + 0.65, wz);
    const crown = new THREE.Mesh(new THREE.ConeGeometry(1.4, 3.0, 10), M.leaf);
    crown.position.set(wx, FLOOR_T + 1.3 + 1.5, wz);
    scene.add(trunk, crown);
  }
  function bench(x, y, alongX = true) {
    const [wx, wz] = P(x, y);
    const seat = new THREE.Mesh(new THREE.BoxGeometry(alongX ? 3 : 0.8, 0.12, alongX ? 0.8 : 3), M.wood);
    seat.position.set(wx, FLOOR_T + 0.48, wz);
    scene.add(seat);
    [-1, 1].forEach((k) => {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(alongX ? 0.12 : 0.7, 0.42, alongX ? 0.7 : 0.12), M.dark);
      leg.position.set(wx + (alongX ? k * 1.3 : 0), FLOOR_T + 0.21, wz + (alongX ? 0 : k * 1.3));
      scene.add(leg);
    });
  }

  const roomFloors = {};
  function buildBuilding() {
    const ground = new THREE.Mesh(new THREE.CircleGeometry(260, 64), M.ground);
    ground.rotation.x = -Math.PI / 2;
    ground.position.y = -PLINTH;
    scene.add(ground);
    slab(F.EXTERIOR, { depth: PLINTH, y: -PLINTH, material: M.base });
    slab(F.LOBBY, { depth: FLOOR_T, material: M.lobby });
    slab(F.CORRIDOR, { depth: FLOOR_T, material: M.lobby });
    slab(F.PASSAGE, { depth: FLOOR_T, material: M.lobby });

    F.ROOMS.forEach((r) => {
      const m = mat(0xe9e6e1, { emissive: new THREE.Color('#7c3aed'), emissiveIntensity: 0 });
      const mesh = slab(r.poly, { depth: FLOOR_T, material: m });
      mesh.userData.roomId = r.id;
      pickTargets.push(mesh);
      roomFloors[r.id] = mesh;
    });

    F.WALLS.forEach((w) => {
      if (w.glass) return glassWall(w);
      const m = segmentBox(w.a, w.b, WALL_H, w.t, w.kind === 'partition' ? M.partition : M.wall, 0, wallGroup);
      addEdges(m, wallGroup);
    });
    F.CORES.forEach(([x0, y0, x1, y1]) => {
      const side = mat(0xd2d2d2);
      const top = mat(0xffffff, { map: hatchTexture((x1 - x0) / 22, (y1 - y0) / 22) });
      const m = block(x0, y0, x1, y1, WALL_H, side);
      m.material = [side, side, top, side, side, side];
    });
    block(...F.RESTROOM, WALL_H, M.restroom);

    // 중정
    const [cx0, cy0, cx1, cy1] = [F.COURT[0][0], F.COURT[0][1], F.COURT[2][0], F.COURT[2][1]];
    const [px0, px1, py0, py1] = [593, 613, 305, 325];
    [[cx0, cy0, px0, py0], [px1, cy0, cx1, py0], [cx0, py1, px0, cy1], [px1, py1, cx1, cy1]]
      .forEach(([x0, y0, x1, y1]) => slab([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], { depth: FLOOR_T, material: M.grass, edges: false }));
    slab([[px0, cy0], [px1, cy0], [px1, py0], [cx1, py0], [cx1, py1], [px1, py1], [px1, cy1],
      [px0, cy1], [px0, py1], [cx0, py1], [cx0, py0], [px0, py0]], { depth: FLOOR_T, material: M.path, edges: false });
    tree(516, 252); tree(689, 252); tree(516, 376); tree(689, 376);
    bench(560, 290); bench(648, 290); bench(560, 340); bench(648, 340);

    // 로비 안내 데스크
    const [dx, dz] = P(603, 505);
    const desk = new THREE.Mesh(new THREE.BoxGeometry(6, 1.1, 1.4), M.wall);
    desk.position.set(dx, FLOOR_T + 0.55, dz);
    const top = new THREE.Mesh(new THREE.BoxGeometry(6.2, 0.08, 1.6), M.wood);
    top.position.set(dx, FLOOR_T + 1.14, dz);
    scene.add(desk, top);
    addEdges(desk, scene);
    bench(500, 520, false); bench(705, 520, false);
  }

  /* ---------------- 작품 걸기 ---------------- */
  const texLoader = new THREE.TextureLoader();
  const glowTex = (() => {
    const c = document.createElement('canvas');
    c.width = c.height = 128;
    const g = c.getContext('2d');
    const grd = g.createRadialGradient(64, 54, 4, 64, 64, 64);
    grd.addColorStop(0, 'rgba(255,240,214,0.85)');
    grd.addColorStop(0.55, 'rgba(255,240,214,0.25)');
    grd.addColorStop(1, 'rgba(255,240,214,0)');
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    const t = new THREE.CanvasTexture(c);
    t.encoding = THREE.sRGBEncoding;
    return t;
  })();
  const glowMat = new THREE.MeshBasicMaterial({ map: glowTex, transparent: true, opacity: 0.35, depthWrite: false, blending: THREE.AdditiveBlending });
  const unitPlane = new THREE.PlaneGeometry(1, 1);
  const unitBox = new THREE.BoxGeometry(1, 1, 1);
  const fixtureGeo = new THREE.BoxGeometry(0.14, 0.1, 0.42);
  const ART = {};   // workId → { work, group, plane, frame, glow, caption, roomId, index, slot, w, h, thumbTex, fullTex, level }

  function captionTexture(num, title, artist) {
    const c = document.createElement('canvas');
    c.width = 256; c.height = 80;
    const g = c.getContext('2d');
    g.fillStyle = '#ffffff'; g.fillRect(0, 0, 256, 80);
    g.fillStyle = '#7c3aed'; g.fillRect(0, 0, 5, 80);
    const font = "'Noto Sans KR', 'Malgun Gothic', 'Apple SD Gothic Neo', sans-serif";
    g.fillStyle = '#18181b';
    g.font = `700 22px ${font}`;
    let t = `${num}. ${title}`;
    while (g.measureText(t).width > 236 && t.length > 4) t = t.slice(0, -2) + '…';
    g.fillText(t, 14, 33);
    g.fillStyle = '#52525b';
    g.font = `400 18px ${font}`;
    g.fillText(artist, 14, 62);
    const tex = new THREE.CanvasTexture(c);
    tex.encoding = THREE.sRGBEncoding;
    tex.anisotropy = Math.min(4, MAX_ANISO);
    return tex;
  }

  function fitArt(e, ratio) {
    const maxW = Math.min(1.9, e.slot.spacing / PX - 0.6);
    let w = maxW, h = w / ratio;
    if (h > ART_MAX_H) { h = ART_MAX_H; w = h * ratio; }
    e.w = w; e.h = h;
    e.plane.scale.set(w, h, 1);
    e.frame.scale.set(w + FRAME_B * 2, h + FRAME_B * 2, FRAME_D);
    e.glow.scale.set(w + 1.6, h + 1.9, 1);
    e.caption.position.set(-w / 2 + 0.26, -h / 2 - FRAME_B - 0.2, 0.012);
  }

  function buildArtworks() {
    F.ROOMS.forEach((r) => {
      const list = S.roomWorks[r.id] || [];
      list.forEach((work, i) => {
        const slot = S.slots[r.id][i];
        const g = new THREE.Group();
        const [x, z] = P(...slot.art);
        g.position.set(x, FLOOR_T + ART_Y, z);
        g.rotation.y = Math.atan2(slot.n[0], slot.n[1]);   // 그룹의 +z가 실내 쪽을 보게

        const frame = new THREE.Mesh(unitBox, M.frame);
        frame.position.z = FRAME_D / 2;
        const plane = new THREE.Mesh(unitPlane, new THREE.MeshBasicMaterial({ color: 0xd4d4d8 }));
        plane.position.z = FRAME_D + 0.003;
        const glow = new THREE.Mesh(unitPlane, glowMat);
        glow.position.set(0, 0.25, 0.004);
        const caption = new THREE.Mesh(new THREE.PlaneGeometry(0.5, 0.156),
          new THREE.MeshBasicMaterial({ map: captionTexture(i + 1, work.title, work.artist) }));
        const fixture = new THREE.Mesh(fixtureGeo, M.fixture);
        fixture.position.set(0, WALL_H - ART_Y - 0.45, 0.21);
        g.add(glow, frame, plane, caption, fixture);
        artGroup.add(g);

        const e = { work, group: g, plane, frame, glow, caption, roomId: r.id, index: i, slot, level: 'none' };
        const iw = work.image.w, ih = work.image.h;
        fitArt(e, iw && ih ? iw / ih : 4 / 3);
        plane.userData.workId = frame.userData.workId = work.id;
        pickTargets.push(plane, frame);
        ART[work.id] = e;
      });
    });
  }

  function applyTexture(e, tex, level) {
    tex.encoding = THREE.sRGBEncoding;
    tex.anisotropy = MAX_ANISO;
    const m = e.plane.material;
    m.map = tex;
    m.color.set(0xffffff);   // 색을 곱하지 않음 → 이미지 원래 색 그대로
    m.needsUpdate = true;
    e.level = level;
    const img = tex.image;
    if (img && img.width && img.height) fitArt(e, img.width / img.height);
  }

  // 썸네일: 현재 전시실 먼저, 동시에 6개씩
  const thumbQueue = [];
  let thumbActive = 0;
  function queueThumbs(firstRoom) {
    const order = [...F.ROOMS].sort((a, b) => (a.id === firstRoom ? -1 : b.id === firstRoom ? 1 : a.id - b.id));
    order.forEach((r) => (S.roomWorks[r.id] || []).forEach((w) => thumbQueue.push(w.id)));
    pumpThumbs();
  }
  function pumpThumbs() {
    while (thumbActive < 6 && thumbQueue.length) {
      const id = thumbQueue.shift();
      const e = ART[id];
      if (!e || e.thumbTex) continue;
      thumbActive++;
      texLoader.load(e.work.image.thumb, (tex) => {
        e.thumbTex = tex;
        if (e.level !== 'full') applyTexture(e, tex, 'thumb');
        thumbActive--; pumpThumbs();
      }, undefined, () => { thumbActive--; pumpThumbs(); });
    }
  }
  // 고해상도: 보고 있는 작품 앞뒤 2점만, 멀어지면 해제(메모리 절약)
  function ensureFull(roomId, index) {
    (S.roomWorks[roomId] || []).forEach((w, i) => {
      const e = ART[w.id];
      if (!e) return;
      const near = Math.abs(i - index) <= 2;
      if (near && e.level !== 'full' && !e.loadingFull) {
        e.loadingFull = true;
        texLoader.load(w.image.full, (tex) => {
          e.loadingFull = false;
          if (S.roomId === roomId && Math.abs(S.index - i) <= 4) { e.fullTex = tex; applyTexture(e, tex, 'full'); }
          else tex.dispose();
        }, undefined, () => { e.loadingFull = false; });
      } else if (!near && Math.abs(i - index) > 4 && e.fullTex) {
        if (e.thumbTex) applyTexture(e, e.thumbTex, 'thumb');
        e.fullTex.dispose();
        e.fullTex = null;
      }
    });
    Object.values(ART).forEach((e) => {
      if (e.roomId !== roomId && e.fullTex) {
        if (e.thumbTex) applyTexture(e, e.thumbTex, 'thumb');
        e.fullTex.dispose(); e.fullTex = null;
      }
    });
  }

  /* ---------------- 전시실 라벨 ---------------- */
  const roomLabels = {};
  function buildLabels() {
    F.ROOMS.forEach((fr) => {
      const r = S.rooms.find((x) => x.id === fr.id);
      if (!r) return;
      const el = document.createElement('button');
      el.className = 'room-label';
      el.innerHTML = `<span class="num">${r.id}</span><span class="txt"><b></b><em></em></span>`;
      el.querySelector('b').textContent = `${r.name} · ${r.theme}`;
      el.querySelector('em').textContent = `작품 ${(S.roomWorks[r.id] || []).length}점`;
      el.addEventListener('click', () => goTo(r.id, 0));
      el.addEventListener('pointerenter', () => setHover(r.id));
      el.addEventListener('pointerleave', () => setHover(null));
      const wrap = document.createElement('div');
      wrap.appendChild(el);
      const obj = new THREE.CSS2DObject(wrap);
      const [x, z] = P(...fr.label);
      obj.position.set(x, WALL_H + 1.2, z);
      scene.add(obj);
      roomLabels[r.id] = el;
    });
  }

  /* ---------------- 카메라 이동 ---------------- */
  const ease = (k) => (k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  let tween = null;

  function fitDistance(w = 92, d = 68) {
    const half = Math.tan(THREE.MathUtils.degToRad(40 / 2));
    return Math.max(d / 2 / half, w / 2 / (half * camera.aspect)) * 1.08;
  }
  const OVERVIEW_DIR = new THREE.Vector3(-0.42, 0.78, 0.8).normalize();
  const OVERVIEW_TARGET = new THREE.Vector3(0, 0, 2);

  function viewFor(roomId, index) {
    const slot = S.slots[roomId][index];
    const e = ART[S.roomWorks[roomId][index].id];
    const [ax, az] = P(...slot.art);
    const [vx, vz] = P(...slot.view);
    const y = FLOOR_T + ART_Y;
    const d = slot.viewDist / PX;
    const h = (e ? e.h : 1.2) + FRAME_B * 2 + 0.25;
    const fov = clamp(THREE.MathUtils.radToDeg(2 * Math.atan((h / SCREEN_FILL) / 2 / d)), 36, 74);
    return { pos: new THREE.Vector3(vx, y + 0.02, vz), target: new THREE.Vector3(ax, y, az), fov, slot };
  }

  function startTween(type, to, dur, onDone) {
    const p0 = camera.position.clone();
    const tw = { type, p0, p1: to.pos.clone(), t0: look.clone(), t1: to.target.clone(), fov0: camera.fov, fov1: to.fov, start: performance.now(), dur, onDone };
    if (type === 'arc') {
      const H = Math.max(p0.y, WALL_H + 14);
      tw.c1 = new THREE.Vector3(p0.x, H, p0.z);
      tw.c2 = new THREE.Vector3(to.pos.x, WALL_H + 10, to.pos.z);
    }
    tween = tw;
    S.animating = true;
    $('navBar').classList.add('busy');
  }
  function bezier(a, b, c, d, t, out) {
    const u = 1 - t;
    return out.set(
      u * u * u * a.x + 3 * u * u * t * b.x + 3 * u * t * t * c.x + t * t * t * d.x,
      u * u * u * a.y + 3 * u * u * t * b.y + 3 * u * t * t * c.y + t * t * t * d.y,
      u * u * u * a.z + 3 * u * u * t * b.z + 3 * u * t * t * c.z + t * t * t * d.z);
  }
  function fadeJump(to, onDone) {
    S.animating = true;
    $('navBar').classList.add('busy');
    $('fade').classList.add('on');
    setTimeout(() => {
      camera.position.copy(to.pos);
      look.copy(to.target);
      camera.fov = to.fov;
      camera.updateProjectionMatrix();
      $('fade').classList.remove('on');
      setTimeout(() => { S.animating = false; $('navBar').classList.remove('busy'); onDone && onDone(); }, 240);
    }, 230);
  }

  function goTo(roomId, index, opts = {}) {
    if (S.animating) return false;
    const list = S.roomWorks[roomId] || [];
    if (!list.length) { toast('이 전시실에는 아직 걸린 작품이 없어요.'); return false; }
    index = clamp(index, 0, list.length - 1);
    const to = viewFor(roomId, index);
    const fromMode = S.mode, fromRoom = S.roomId, fromIndex = S.index;
    if (fromMode === 'overview') look.copy(controls.target);
    S.mode = 'view'; S.roomId = roomId; S.index = index;
    controls.enabled = false;
    setAutoRotate(false);
    setModeUI();
    updateCard();
    updateCounter();
    ensureFull(roomId, index);

    const done = () => { $('navBar').classList.remove('busy'); };
    if (opts.instant) {
      camera.position.copy(to.pos); look.copy(to.target); camera.fov = to.fov; camera.updateProjectionMatrix();
    } else if (fromMode !== 'view' || fromRoom !== roomId) {
      startTween('arc', to, 1900, done);
    } else {
      const fromSlot = S.slots[roomId][fromIndex];
      if (F.blocked(fromSlot.view, to.slot.view)) fadeJump(to, done);
      else {
        const dist = camera.position.distanceTo(to.pos);
        startTween('line', to, clamp(650 + dist * 70, 700, 1500), done);
      }
    }
    moveSpot(roomId, index);
    return true;
  }

  function moveSpot(roomId, index) {
    const e = ART[S.roomWorks[roomId][index].id];
    if (!e) return;
    const n = e.slot.n;
    const [x, z] = P(...e.slot.art);
    spot.position.set(x + n[0] * 1.6, FLOOR_T + WALL_H - 0.2, z + n[1] * 1.6);
    spot.target.position.set(x, FLOOR_T + ART_Y, z);
    spot.intensity = 0.9;
  }

  function overview(kind = 'overview') {
    if (S.animating) return;
    if (S.mode === 'view') look.copy(look);
    else look.copy(controls.target);
    S.mode = 'overview';
    spot.intensity = 0;
    setModeUI();
    const pos = kind === 'top'
      ? new THREE.Vector3(0, fitDistance() * 1.12 + 6, 0.01)
      : OVERVIEW_DIR.clone().multiplyScalar(fitDistance() * 1.12);
    const target = kind === 'top' ? new THREE.Vector3(0, 0, 0) : OVERVIEW_TARGET.clone();
    startTween('line', { pos, target, fov: 40 }, 1400, () => {
      $('navBar').classList.remove('busy');
      controls.target.copy(target);
      controls.enabled = true;
      controls.update();
    });
  }

  function roomOrder() { return S.rooms.map((r) => r.id).filter((id) => (S.roomWorks[id] || []).length); }

  function step(dir, source) {
    if (S.animating) { if (source === 'gesture') flashGesture('이동 중이라 이번 손짓은 건너뛰었어요'); return false; }
    if (!$('searchModal').hidden || !$('lightbox').hidden) {
      if (source === 'gesture') toast('창을 닫으면 손짓으로 넘길 수 있어요');
      return false;
    }
    const order = roomOrder();
    if (!order.length) return false;
    if (S.mode !== 'view') {
      const r = S.roomId && order.includes(S.roomId) ? S.roomId : order[0];
      return goTo(r, dir > 0 ? (S.roomId === r ? S.index : 0) : (S.roomWorks[r].length - 1));
    }
    let r = S.roomId, i = S.index + dir;
    if (i >= S.roomWorks[r].length || i < 0) {
      const k = order.indexOf(r);
      r = order[(k + (dir > 0 ? 1 : -1) + order.length) % order.length];
      i = dir > 0 ? 0 : S.roomWorks[r].length - 1;
      const room = S.rooms.find((x) => x.id === r);
      toast(`${room.name} · ${room.theme}(으)로 이동해요`);
    }
    const ok = goTo(r, i);
    if (ok && source === 'gesture') flashGesture();
    return ok;
  }

  /* ---------------- 화면 갱신 ---------------- */
  function setModeUI() {
    const view = S.mode === 'view';
    $('introCard').hidden = true;
    $('roomPanel').hidden = view;
    $('navBar').hidden = !view;
    $('btnOverview').hidden = !view;
    $('workCard').hidden = !view || S.cardHidden;
    $('cardOpen').hidden = !view || !S.cardHidden;
    document.body.classList.toggle('card-on', view && !S.cardHidden);
    labelRenderer.domElement.style.display = view ? 'none' : '';
    $('tooltip').classList.remove('show');
    applyViewOffset();
  }

  function applyViewOffset() {
    const W = innerWidth, H = innerHeight;
    camera.aspect = viewAspect();
    if (S.mode === 'view' && !S.cardHidden && W > 860) {
      const card = $('workCard').getBoundingClientRect();
      const dx = (card.width + 20) / 2;
      camera.setViewOffset(W, H, dx, 0, W, H);
    } else if (S.mode === 'view' && !S.cardHidden && W <= 860) {
      camera.setViewOffset(W, H, 0, Math.round(H * 0.2), W, H);
    } else {
      camera.clearViewOffset();
    }
    camera.updateProjectionMatrix();
  }

  const STATUS_CLASS = { '보관 중': 'b-stored', '설치됨': 'b-installed', '확인 필요': 'b-check' };
  function fmtDate(s) { return s ? String(s).slice(0, 10) : ''; }

  function updateCard() {
    const w = S.roomWorks[S.roomId][S.index];
    const room = S.rooms.find((r) => r.id === S.roomId);
    $('wcRoom').textContent = `${room.name} · ${room.theme}`;
    $('wcExample').hidden = !w.is_example;
    $('wcTitle').textContent = w.title;
    $('wcArtist').textContent = w.artist;
    $('wcArtistNote').textContent = /○/.test(w.artist) ? '작가 표시명(익명)' : '작가';
    $('wcMeta').textContent = [w.year && `${w.year}년`, w.material, w.size && w.size.replace(/\s*[*xX×]\s*/g, '×')].filter(Boolean).join(' · ') || '제작 정보 준비 중';
    const intro = $('wcIntro');
    if (w.intro) { intro.textContent = w.intro; intro.classList.remove('pending'); }
    else { intro.textContent = '작품 소개문은 담당자가 제공 자료를 검토·승인한 뒤 공개돼요.'; intro.classList.add('pending'); }
    const st = $('wcStatus');
    st.textContent = w.status || '확인 필요';
    st.className = 'badge ' + (STATUS_CLASS[w.status] || 'b-check');
    let loc = w.location;
    if (!loc || w.status === '확인 필요') loc = loc || '담당자가 현재 위치를 확인하고 있어요';
    $('wcLocation').textContent = loc;
    $('wcLocDate').textContent = `담당자 입력 기준 · ${fmtDate(w.location_updated_at)}`;
    $('wcVirtual').textContent = `${room.name} ${S.index + 1}번 자리 (온라인 전시관 안의 위치)`;
    const liked = S.likes.has(w.id);
    $('likeBtn').setAttribute('aria-pressed', liked);
    $('likeLabel').textContent = liked ? '관심 저장됨' : '관심 저장';
    $('likeCount').textContent = w.likes || 0;
    $('commentInput').value = '';
    $('commentCount').textContent = `0 / ${$('commentInput').maxLength}`;
  }

  function updateCounter() {
    const room = S.rooms.find((r) => r.id === S.roomId);
    const n = S.roomWorks[S.roomId].length;
    $('cntCur').textContent = S.index + 1;
    $('cntTotal').textContent = n;
    let before = 0;
    for (const id of roomOrder()) { if (id === S.roomId) break; before += S.roomWorks[id].length; }
    $('cntRoom').textContent = `${room.name} · 전체 ${before + S.index + 1} / ${S.total}`;
  }

  /* ---------------- 호버 / 클릭 ---------------- */
  let hoveredRoom = null;
  function setHover(id) {
    if (hoveredRoom === id) return;
    hoveredRoom = id;
    F.ROOMS.forEach((r) => {
      const on = r.id === id && S.mode === 'overview';
      roomFloors[r.id].material.emissiveIntensity = on ? 0.18 : 0;
      roomLabels[r.id] && roomLabels[r.id].classList.toggle('is-hover', on);
      const b = document.querySelector(`.room-btn[data-room="${r.id}"]`);
      b && b.classList.toggle('is-hover', on);
    });
  }
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  function pick(ev) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.set(((ev.clientX - rect.left) / rect.width) * 2 - 1, -((ev.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(pickTargets, false).find((h) => h.object.visible && h.object.parent.visible);
    return hit ? hit.object.userData : null;
  }
  const tooltip = $('tooltip');
  renderer.domElement.addEventListener('pointermove', (ev) => {
    if (ev.pointerType === 'touch' || tween) return;
    const u = pick(ev) || {};
    let text = '';
    if (S.mode === 'overview') {
      const rid = u.roomId || (u.workId && ART[u.workId] && ART[u.workId].roomId) || null;
      setHover(rid);
      if (rid) {
        const r = S.rooms.find((x) => x.id === rid);
        text = `${r.name} · ${r.theme} · 작품 ${(S.roomWorks[rid] || []).length}점 — 클릭해서 입장`;
      }
    } else if (u.workId && ART[u.workId] && ART[u.workId].roomId === S.roomId) {
      const e = ART[u.workId];
      if (e.index !== S.index) text = `${e.index + 1}. ${e.work.title} — 클릭해서 이동`;
    }
    renderer.domElement.style.cursor = text ? 'pointer' : '';
    if (text) {
      tooltip.textContent = text;
      tooltip.style.left = `${ev.clientX}px`;
      tooltip.style.top = `${ev.clientY}px`;
      tooltip.classList.add('show');
    } else tooltip.classList.remove('show');
  });
  renderer.domElement.addEventListener('pointerleave', () => { setHover(null); tooltip.classList.remove('show'); });
  let downAt = null;
  renderer.domElement.addEventListener('pointerdown', (ev) => { downAt = [ev.clientX, ev.clientY]; });
  renderer.domElement.addEventListener('pointerup', (ev) => {
    if (!downAt || Math.hypot(ev.clientX - downAt[0], ev.clientY - downAt[1]) > 5) return;
    const u = pick(ev);
    if (!u) return;
    if (S.mode === 'overview') {
      const rid = u.roomId || (u.workId && ART[u.workId] && ART[u.workId].roomId);
      if (rid) goTo(rid, u.workId ? ART[u.workId].index : 0);
    } else if (u.workId && ART[u.workId] && ART[u.workId].roomId === S.roomId) {
      goTo(S.roomId, ART[u.workId].index);
    }
  });
  controls.addEventListener('start', () => setAutoRotate(false));

  /* ---------------- 패널 · 버튼 ---------------- */
  function setAutoRotate(on) {
    controls.autoRotate = on;
    const b = document.querySelector('[data-view="rotate"]');
    b && b.setAttribute('aria-pressed', on);
  }
  let wallTarget = 1;
  function buildUI() {
    const list = $('roomList');
    S.rooms.forEach((r) => {
      const li = document.createElement('li');
      const b = document.createElement('button');
      b.className = 'room-btn';
      b.dataset.room = r.id;
      b.innerHTML = '<span class="num"></span><span class="meta"><span></span><small></small></span><span class="cnt"></span>';
      b.querySelector('.num').textContent = r.id;
      b.querySelector('.meta span').textContent = `${r.name} · ${r.theme}`;
      b.querySelector('.meta small').textContent = r.subtitle || '';
      b.querySelector('.cnt').textContent = `${(S.roomWorks[r.id] || []).length}점`;
      b.addEventListener('click', () => goTo(r.id, 0));
      b.addEventListener('pointerenter', () => setHover(r.id));
      b.addEventListener('pointerleave', () => setHover(null));
      li.appendChild(b);
      list.appendChild(li);
    });
    $('introStats').textContent = `전시실 ${roomOrder().length}곳 · 작품 ${S.total}점 · 작가 ${new Set(S.works.map((w) => w.artist)).size}명`;
    $('brandSub').textContent = `장애 예술인 작품 ${S.total}점 온라인 전시`;

    $('enterBtn').addEventListener('click', () => goTo(roomOrder()[0], 0));
    $('btnOverview').addEventListener('click', () => overview());
    $('prevBtn').addEventListener('click', () => step(-1, 'button'));
    $('nextBtn').addEventListener('click', () => step(1, 'button'));
    $('cardClose').addEventListener('click', () => { S.cardHidden = true; setModeUI(); });
    $('cardOpen').addEventListener('click', () => { S.cardHidden = false; setModeUI(); });
    $('zoomBtn').addEventListener('click', () => openLightbox(S.roomWorks[S.roomId][S.index]));
    $('lbClose').addEventListener('click', closeLightbox);
    $('lightbox').addEventListener('click', (e) => { if (e.target.id === 'lightbox') closeLightbox(); });

    document.querySelectorAll('[data-view]').forEach((b) => b.addEventListener('click', () => {
      const v = b.dataset.view;
      if (v === 'overview' || v === 'top') { setAutoRotate(false); overview(v); }
      if (v === 'walls') { wallTarget = wallTarget === 1 ? 0.28 : 1; b.setAttribute('aria-pressed', wallTarget !== 1); }
      if (v === 'rotate') setAutoRotate(!controls.autoRotate);
    }));

    // 관심 저장
    $('likeBtn').addEventListener('click', async () => {
      const w = S.roomWorks[S.roomId][S.index];
      const on = !S.likes.has(w.id);
      try {
        const r = await api('/api/react/like', { id: w.id, on });
        w.likes = r.likes;
        on ? S.likes.add(w.id) : S.likes.delete(w.id);
        saveLikes();
        updateLikeCount();
        updateCard();
        if (on) { $('likeBtn').classList.remove('pop'); void $('likeBtn').offsetWidth; $('likeBtn').classList.add('pop'); }
        toast(on ? '관심 작품에 저장했어요' : '관심 저장을 취소했어요');
      } catch (err) { toast('저장하지 못했어요: ' + err.message); }
    });
    // 한마디
    const ci = $('commentInput');
    ci.addEventListener('input', () => { $('commentCount').textContent = `${ci.value.length} / ${ci.maxLength}`; });
    $('commentForm').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const text = ci.value.trim();
      if (!text) { ci.focus(); return; }
      const w = S.roomWorks[S.roomId][S.index];
      try {
        await api('/api/react/comment', { id: w.id, text });
        ci.value = '';
        $('commentCount').textContent = `0 / ${ci.maxLength}`;
        ci.blur();
        toast('한마디가 담당자에게 전달됐어요. 고마워요!');
      } catch (err) { toast(err.message); }
    });

    // 검색 / 내 관심
    $('btnSearch').addEventListener('click', () => openSearch('search'));
    $('btnLikes').addEventListener('click', () => openSearch('likes'));
    $('tabSearch').addEventListener('click', () => openSearch('search'));
    $('tabLikes').addEventListener('click', () => openSearch('likes'));
    $('searchClose').addEventListener('click', closeSearch);
    $('searchModal').addEventListener('click', (e) => { if (e.target.id === 'searchModal') closeSearch(); });
    $('searchInput').addEventListener('input', renderSearch);
    updateLikeCount();
  }

  function updateLikeCount() { $('myLikeCount').textContent = [...S.likes].filter((id) => S.byId[id]).length; }

  /* ---------------- 검색 ---------------- */
  const norm = (s) => String(s || '').toLowerCase().replace(/\s+/g, '');
  function openSearch(tab) {
    S.searchTab = tab;
    $('searchModal').hidden = false;
    $('tabSearch').setAttribute('aria-selected', tab === 'search');
    $('tabLikes').setAttribute('aria-selected', tab === 'likes');
    $('searchInput').hidden = tab !== 'search';
    renderSearch();
    if (tab === 'search') setTimeout(() => $('searchInput').focus(), 30);
  }
  function closeSearch() { $('searchModal').hidden = true; }
  function renderSearch() {
    const ul = $('searchList');
    ul.innerHTML = '';
    let items;
    if (S.searchTab === 'likes') {
      items = [...S.likes].map((id) => S.byId[id]).filter(Boolean);
      $('searchInfo').textContent = items.length ? `관심 저장한 작품 ${items.length}점 (이 브라우저에만 기억돼요)` : '아직 관심 저장한 작품이 없어요. 작품 정보의 ♡ 관심 저장을 눌러 보세요.';
    } else {
      const q = norm($('searchInput').value);
      items = S.works.filter((w) => !q || w._search.includes(q));
      $('searchInfo').textContent = q ? `'${$('searchInput').value.trim()}' 검색 결과 ${items.length}점` : `전체 작품 ${items.length}점 · 작품명, 작가 표시명, 주제로 찾을 수 있어요`;
    }
    items.slice(0, 80).forEach((w) => {
      const li = document.createElement('li');
      const b = document.createElement('button');
      const img = document.createElement('img');
      img.src = w.image.thumb; img.alt = ''; img.loading = 'lazy';
      const t = document.createElement('span'); t.className = 't';
      const tb = document.createElement('b'); tb.textContent = w.title;
      const ts = document.createElement('small'); ts.textContent = [w.artist, w.year, w.theme].filter(Boolean).join(' · ');
      t.append(tb, ts);
      const where = document.createElement('span'); where.className = 'where';
      const e = ART[w.id];
      const room = S.rooms.find((r) => r.id === w.room);
      where.textContent = e ? `${room ? room.name : ''} ${e.index + 1}번` : '크게 보기';
      b.append(img, t, where);
      b.addEventListener('click', () => {
        closeSearch();
        if (e) goTo(e.roomId, e.index); else openLightbox(w);
      });
      li.appendChild(b);
      ul.appendChild(li);
    });
  }

  /* ---------------- 크게 보기 ---------------- */
  function openLightbox(w) {
    $('lbImg').src = w.image.full;
    $('lbImg').alt = `${w.title} — ${w.artist}`;
    $('lbCaption').textContent = `${w.title} · ${w.artist}${w.year ? ' · ' + w.year : ''}  (원본 비율·색 그대로)`;
    $('lightbox').hidden = false;
  }
  function closeLightbox() { $('lightbox').hidden = true; $('lbImg').removeAttribute('src'); }

  /* ---------------- 키보드 ---------------- */
  window.addEventListener('keydown', (e) => {
    if (e.target.matches && e.target.matches('input, textarea')) {
      if (e.key === 'Escape') e.target.blur();
      return;
    }
    if (e.key === 'Escape') {
      if (!$('lightbox').hidden) return closeLightbox();
      if (!$('searchModal').hidden) return closeSearch();
      if (S.mode === 'view') return overview();
    }
    if (!$('lightbox').hidden || !$('searchModal').hidden) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); step(1, 'key'); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1, 'key'); }
    else if (e.key === '/') { e.preventDefault(); openSearch('search'); }
    else if (/^[1-9]$/.test(e.key)) {
      const id = Number(e.key);
      if ((S.roomWorks[id] || []).length) goTo(id, 0);
    }
  });

  /* ---------------- 제스처 (Python 서버와 연결) ---------------- */
  const G_LABEL = { off: '웹캠 꺼짐', starting: '준비 중', searching: '손 찾는 중', detected: '손 인식됨', moved: '이동', error: '오류', offline: '서버 연결 안 됨' };
  const G_PARAMS = { mode: 'tilt', tilt_angle: 30, neutral_angle: 12, hold_time: 0.2, cooldown: 1, video: true };
  const TILT_RANGE = 70;   // 표시 범위 ±70°
  function applyGesture(d) {
    S.gState = d.state;
    const st = $('gState');
    st.className = 'state s-' + d.state;
    st.textContent = d.state === 'moved' ? (d.message || '이동') : (G_LABEL[d.state] || d.state);
    const on = !['off', 'error', 'offline'].includes(d.state);
    const tg = $('gestureToggle');
    tg.textContent = on ? '제스처 모드 종료' : '제스처 모드 시작';
    tg.classList.toggle('primary', !on);
    tg.disabled = d.state === 'offline';
    const msg = $('gMsg');
    if (d.message) msg.textContent = d.message;
    else if (d.state === 'off') msg.textContent = "카메라는 '제스처 모드 시작'을 눌렀을 때만 켜져요. 영상은 이 PC 안에서만 보여 주고 저장하지 않아요.";
    msg.classList.toggle('err', d.state === 'error');
    if (d.params) { Object.assign(G_PARAMS, d.params); fillSliders(); }
    if (!on) setHand(null);
    setCamera(on && G_PARAMS.video);
    layoutRight();
  }
  // 카메라 화면: 서버가 이 PC 브라우저에만 MJPEG로 보내 줌
  let camOn = false;
  function setCamera(on) {
    if (on === camOn) return;
    camOn = on;
    const img = $('camView');
    $('camBox').hidden = !on;
    if (on) img.src = '/api/gesture/video?t=' + Date.now();
    else img.removeAttribute('src');
  }
  $('camView').addEventListener('error', () => { if (camOn) setTimeout(() => { if (camOn) $('camView').src = '/api/gesture/video?t=' + Date.now(); }, 1500); });
  function setHand(d) {
    const dot = $('tiltDot');
    if (!d || d.angle === null || d.angle === undefined) {
      dot.classList.remove('on', 'ready');
      $('tiltVal').textContent = d && d.x !== null && d.x !== undefined ? '손 인식됨' : '손 없음';
      return;
    }
    const a = clamp(d.angle, -TILT_RANGE, TILT_RANGE);
    dot.classList.add('on');
    dot.classList.toggle('ready', !!d.ready);
    dot.style.left = `${50 + (a / TILT_RANGE) * 50}%`;
    $('tiltVal').textContent = `${d.angle > 0 ? '+' : ''}${Math.round(d.angle)}°${d.ready ? '' : ' · 손을 세워 주세요'}`;
  }
  function paintZones() {
    const tr = document.querySelector('.tilt-track');
    const tz = Math.max(0, (TILT_RANGE - G_PARAMS.tilt_angle) / (2 * TILT_RANGE)) * 100;
    tr.style.setProperty('--tz', tz + '%');
    tr.style.setProperty('--nz', (G_PARAMS.neutral_angle / TILT_RANGE) * 100 + '%');
  }
  function flashGesture(msg) {
    const p = $('gesturePanel');
    p.classList.remove('flash'); void p.offsetWidth; p.classList.add('flash');
    if (msg) toast(msg, 1500);
  }
  let sliding = false;
  function fillSliders() {
    if (sliding) return;
    $('sMode').value = G_PARAMS.mode;
    $('sVideo').checked = !!G_PARAMS.video;
    $('sTilt').value = G_PARAMS.tilt_angle; $('sHold').value = G_PARAMS.hold_time; $('sCool').value = G_PARAMS.cooldown;
    showSliderValues();
  }
  function showSliderValues() {
    $('oTilt').textContent = `${$('sTilt').value}°`;
    $('oHold').textContent = `${Number($('sHold').value).toFixed(2)}초`;
    $('oCool').textContent = `${Number($('sCool').value).toFixed(1)}초`;
    G_PARAMS.tilt_angle = Number($('sTilt').value);
    paintZones();
  }
  // 제스처 패널 높이에 맞춰 아래 패널(전시실 목록·작품 카드) 위치를 내림
  function layoutRight() {
    requestAnimationFrame(() => {
      const r = $('gesturePanel').getBoundingClientRect();
      if (innerWidth > 860) document.documentElement.style.setProperty('--gp-h', Math.round(r.bottom + 12) + 'px');
      applyViewOffset();
    });
  }
  window.addEventListener('resize', layoutRight);
  function connectGesture() {
    $('gestureToggle').addEventListener('click', async () => {
      const on = !['off', 'error', 'offline'].includes(S.gState);
      try {
        const d = await api(on ? '/api/gesture/stop' : '/api/gesture/start', {});
        applyGesture(d);
      } catch (err) { toast('제스처 모드를 바꾸지 못했어요: ' + err.message); }
    });
    $('gSettingsBtn').addEventListener('click', () => {
      const box = $('gSettings');
      box.hidden = !box.hidden;
      $('gSettingsBtn').setAttribute('aria-expanded', !box.hidden);
      layoutRight();
    });
    let cfgTimer = null;
    const saveCfg = (body) => {
      sliding = true;
      clearTimeout(cfgTimer);
      cfgTimer = setTimeout(() => api('/api/gesture/config', body)
        .then(() => toast('인식 설정을 저장했어요', 1200)).catch((e) => toast(e.message))
        .finally(() => { sliding = false; }), 350);
    };
    ['sTilt', 'sHold', 'sCool'].forEach((id) => $(id).addEventListener('input', () => {
      showSliderValues();
      saveCfg({ tilt_angle: Number($('sTilt').value), hold_time: Number($('sHold').value), cooldown: Number($('sCool').value) });
    }));
    $('sMode').addEventListener('change', () => saveCfg({ mode: $('sMode').value }));
    $('sVideo').addEventListener('change', () => {
      G_PARAMS.video = $('sVideo').checked;
      setCamera(G_PARAMS.video && !['off', 'error', 'offline'].includes(S.gState));
      layoutRight();
      saveCfg({ video: G_PARAMS.video });
    });
    document.querySelectorAll('[data-test]').forEach((b) => b.addEventListener('click', () => {
      api('/api/gesture/test', { cmd: b.dataset.test }).catch((e) => toast(e.message));
    }));
    paintZones();
    layoutRight();

    if (!window.EventSource) { applyGesture({ state: 'offline', message: '이 브라우저는 제스처 연결을 지원하지 않아요.' }); return; }
    const es = new EventSource('/api/gesture/stream');
    es.addEventListener('status', (e) => {
      const d = JSON.parse(e.data);
      if (d.id && d.id > S.lastCmdId && S.lastCmdId === 0) S.lastCmdId = d.id;
      applyGesture(d);
    });
    es.addEventListener('hand', (e) => setHand(JSON.parse(e.data)));
    es.addEventListener('command', (e) => {
      const ev = JSON.parse(e.data);
      if (ev.id <= S.lastCmdId) return;              // 같은 명령 두 번 실행 방지
      S.lastCmdId = ev.id;
      if (Date.now() - ev.ts > 2500) return;          // 오래된 명령(재연결 등)은 무시
      step(ev.cmd === 'NEXT' ? 1 : -1, 'gesture');
      if (ev.test) toast(`테스트 명령 ${ev.cmd} 받음`, 1200);
    });
    es.onerror = () => { if (es.readyState !== EventSource.OPEN) applyGesture({ state: 'offline', message: '서버와 연결이 끊겼어요. 서버 창이 켜져 있는지 확인해 주세요. (자동 재연결 중)' }); };
  }

  /* ---------------- 렌더 루프 ---------------- */
  window.addEventListener('resize', () => {
    renderer.setSize(innerWidth, innerHeight);
    labelRenderer.setSize(innerWidth, innerHeight);
    applyViewOffset();
  });

  const tmp = new THREE.Vector3();
  function animate() {
    requestAnimationFrame(animate);
    if (tween) {
      const k = Math.min(1, (performance.now() - tween.start) / tween.dur);
      const e = ease(k);
      if (tween.type === 'arc') camera.position.copy(bezier(tween.p0, tween.c1, tween.c2, tween.p1, e, tmp));
      else camera.position.lerpVectors(tween.p0, tween.p1, e);
      look.lerpVectors(tween.t0, tween.t1, e);
      camera.fov = tween.fov0 + (tween.fov1 - tween.fov0) * e;
      camera.updateProjectionMatrix();
      if (k === 1) {
        const done = tween.onDone;
        tween = null;
        S.animating = false;
        done && done();
      }
    }
    if (tween || S.mode === 'view') camera.lookAt(look);
    else controls.update();

    wallGroup.scale.y += (wallTarget - wallGroup.scale.y) * 0.12;
    artGroup.visible = wallGroup.scale.y > 0.6;
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
  }

  /* ---------------- 시작 ---------------- */
  async function init() {
    let data;
    try {
      data = await api('/api/gallery');
    } catch (err) {
      $('loadingText').innerHTML = '서버에 연결할 수 없어요.<br>폴더의 <b>시작하기.bat</b>(또는 <code>python server.py</code>)로 실행한 뒤 열린 주소로 접속해 주세요.';
      return;
    }
    S.rooms = data.rooms;
    S.works = data.works;
    S.works.forEach((w) => {
      S.byId[w.id] = w;
      const room = S.rooms.find((r) => r.id === w.room);
      w._search = norm([w.title, w.artist, w.theme, w.year, room && room.name, room && room.theme].join(' '));
      (S.roomWorks[w.room] = S.roomWorks[w.room] || []).push(w);
    });
    const needs = {};
    Object.keys(S.roomWorks).forEach((k) => { needs[k] = S.roomWorks[k].length; });
    S.slots = F.computeSlots(needs);
    // 벽 자리가 모자라면 남는 작품은 '크게 보기'로만 (관리자 페이지에서 전시실 배정 조정)
    Object.keys(S.roomWorks).forEach((k) => {
      const cap = (S.slots[k] || []).length;
      if (S.roomWorks[k].length > cap) {
        S.overflow.push(...S.roomWorks[k].slice(cap));
        S.roomWorks[k] = S.roomWorks[k].slice(0, cap);
        console.warn(`전시실 ${k}: 자리 ${cap}개보다 작품이 많아 ${S.overflow.length}점은 검색·크게 보기로만 볼 수 있어요.`);
      }
    });
    S.total = roomOrder().reduce((s, id) => s + S.roomWorks[id].length, 0);

    buildBuilding();
    try { await document.fonts.ready; } catch (e) { /* 글꼴 없어도 진행 */ }
    buildArtworks();
    buildLabels();
    buildUI();
    queueThumbs(roomOrder()[0]);

    camera.position.copy(OVERVIEW_DIR.clone().multiplyScalar(fitDistance() * 1.12));
    controls.target.copy(OVERVIEW_TARGET);
    look.copy(OVERVIEW_TARGET);
    setAutoRotate(true);
    animate();
    connectGesture();
    requestAnimationFrame(() => $('loading').classList.add('done'));

    // 주소 뒤에 #room=2&i=3 을 붙이면 그 작품에서 시작 (시연 준비용)
    const m = location.hash.match(/room=(\d+)(?:&i=(\d+))?/);
    if (m && S.roomWorks[m[1]]) setTimeout(() => goTo(Number(m[1]), Number(m[2] || 1) - 1), 300);

    window.__yeoun = { S, goTo, step, overview, ART, camera };   // 디버그·테스트용
  }
  init();
})();
