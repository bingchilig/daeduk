/* =========================================================================
 * 여운 — 전시관 평면 데이터와 2D 기하 계산 (Three.js 없이도 동작)
 *  - 원본: DAEDUK/3d/main.js 의 평면도 좌표(픽셀, x →, y ↓)를 그대로 사용
 *  - 10px = 1 unit(약 1m). P()가 3D 월드 좌표(x, z)로 바꾼다.
 *  - 각 전시실 벽을 따라 '작품 걸 자리(slot)'와 '감상 지점(viewpoint)'을 계산한다.
 * ========================================================================= */
(function (root) {
  'use strict';

  const PX = 10, CX = 652, CY = 372;
  const WALL_H = 4, FLOOR_T = 0.12, PLINTH = 0.6;
  const P = (x, y) => [(x - CX) / PX, (y - CY) / PX];

  const arc = (x0, x1, y, rise, n = 18) => {
    const pts = [];
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      pts.push([x0 + (x1 - x0) * t, y - rise * Math.sin(Math.PI * t)]);
    }
    return pts;
  };
  const ARC = arc(545, 697, 590, 18); // 1전시실 입구의 곡선

  // 전시실 다각형 — 이름·테마는 data/artworks.json(관리자 페이지)에서 가져온다
  const ROOMS = [
    { id: 1, poly: [[440, 590], ...ARC, [765, 590], [765, 692], [440, 692]], label: [603, 640], entry: [620, 600] },
    { id: 2, poly: [[337, 158], [440, 158], [440, 692], [335, 692], [335, 640], [222, 640],
                    [222, 265], [327, 265], [327, 212], [337, 212]], label: [388, 420], entry: [440, 514] },
    { id: 3, poly: [[808, 255], [890, 255], [890, 470], [975, 470], [975, 640], [920, 640],
                    [920, 692], [765, 692], [765, 455], [793, 455], [793, 428]], label: [850, 560], entry: [779, 497] },
    { id: 4, poly: [[890, 255], [1015, 255], [1015, 215], [1083, 190], [1083, 428],
                    [975, 428], [975, 470], [890, 470]], label: [990, 345], entry: [890, 360] },
    { id: 5, poly: [[765, 103], [1083, 103], [1083, 190], [1015, 215], [1015, 255],
                    [808, 255], [808, 218], [765, 218]], label: [930, 160], entry: [790, 236] },
  ];

  const EXTERIOR = [[337, 158], [440, 158], [440, 170], [765, 170], [765, 103], [870, 103],
    [870, 52], [1083, 52], [1083, 428], [975, 428], [975, 640], [920, 640], [920, 692],
    [335, 692], [335, 640], [222, 640], [222, 212], [337, 212]];
  const COURT = [[440, 200], [765, 200], [765, 428], [440, 428]];
  const LOBBY = [[440, 428], [793, 428], [793, 455], [765, 455], [765, 590],
    ...ARC.slice().reverse(), [440, 590]];
  const CORRIDOR = [[440, 170], [765, 170], [765, 200], [440, 200]];
  const PASSAGE = [[765, 218], [808, 218], [793, 428], [765, 428]];

  const EXT_T = 0.4, INNER_T = 0.25, GLASS_T = 0.18, PART_T = 0.3;
  const EXT_IN = EXT_T * PX;

  const CORES = [[222 + EXT_IN, 212 + EXT_IN, 327, 265], [870 + EXT_IN, 52 + EXT_IN, 1083 - EXT_IN, 103]];
  const RESTROOM = [440 + (INNER_T * PX) / 2, 590, 500, 640];

  const INNER_WALLS = [
    [[440, 200], [440, 468]],
    [[440, 560], [440, 692]],
    [[500, 590], [545, 590]], [[697, 590], [765, 590]],
    [[765, 540], [765, 692]],
    [[765, 455], [793, 455]], [[793, 428], [793, 455]],
    [[808, 255], [793, 428]],
    [[765, 200], [765, 218]],
    [[822, 255], [1015, 255], 0.55],
    [[1015, 215], [1015, 255]],
    [[890, 255], [890, 320]], [[890, 400], [890, 470]], [[890, 470], [975, 470]],
  ];
  const GLASS_WALLS = [[[440, 200], [765, 200]], [[765, 218], [765, 428]], [[440, 428], [765, 428]]];

  // 작품을 더 걸기 위한 가벽(양면). 출입구·동선을 막지 않는 곳에 둔다.
  const PARTITIONS = [
    { room: 2, a: [281, 330], b: [281, 470] },
    { room: 2, a: [281, 530], b: [281, 600] },
    { room: 2, a: [388, 250], b: [388, 380] },
    { room: 3, a: [848, 330], b: [848, 430] },
    { room: 3, a: [810, 600], b: [880, 600] },
    { room: 5, a: [880, 160], b: [1000, 160] },
  ];

  /* ---------- 2D 기하 ---------- */
  function inside([px, py], poly) {
    let c = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const [xi, yi] = poly[i], [xj, yj] = poly[j];
      if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) c = !c;
    }
    return c;
  }
  function segDist(p, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const t = Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
  }
  const add = (p, v, k) => [p[0] + v[0] * k, p[1] + v[1] * k];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1];
  const edgesOf = (poly) => poly.map((p, i) => [p, poly[(i + 1) % poly.length]]);
  const rectEdges = ([x0, y0, x1, y1]) => edgesOf([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]);
  function segIntersect(p1, p2, p3, p4) {
    const d = (p2[0] - p1[0]) * (p4[1] - p3[1]) - (p2[1] - p1[1]) * (p4[0] - p3[0]);
    if (Math.abs(d) < 1e-9) return false;
    const t = ((p3[0] - p1[0]) * (p4[1] - p3[1]) - (p3[1] - p1[1]) * (p4[0] - p3[0])) / d;
    const u = ((p3[0] - p1[0]) * (p2[1] - p1[1]) - (p3[1] - p1[1]) * (p2[0] - p1[0])) / d;
    return t > 0.001 && t < 0.999 && u > 0.001 && u < 0.999;
  }
  function centroid(poly) {
    let a = 0, x = 0, y = 0;
    edgesOf(poly).forEach(([p, q]) => {
      const c = p[0] * q[1] - q[0] * p[1];
      a += c; x += (p[0] + q[0]) * c; y += (p[1] + q[1]) * c;
    });
    a /= 2;
    return [x / (6 * a), y / (6 * a)];
  }

  function insetLoop(poly, d) {
    const lines = edgesOf(poly).map(([a, b]) => {
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const u = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
      let n = [-u[1], u[0]];
      if (!inside(add([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], n, 3), poly)) n = [-n[0], -n[1]];
      return { p: add(a, n, d), u };
    });
    return lines.map((L2, i) => {
      const L1 = lines[(i - 1 + lines.length) % lines.length];
      const det = L1.u[0] * L2.u[1] - L1.u[1] * L2.u[0];
      if (Math.abs(det) < 1e-9) return L2.p;
      const t = ((L2.p[0] - L1.p[0]) * L2.u[1] - (L2.p[1] - L1.p[1]) * L2.u[0]) / det;
      return add(L1.p, L1.u, t);
    });
  }

  // 벽 접합부 계산 (원본 main.js와 동일) — 벽끼리 파고들지 않게 끝점을 다듬는다
  function resolveWalls(defs) {
    const walls = defs.map((w) => {
      const len = Math.hypot(w.b[0] - w.a[0], w.b[1] - w.a[1]);
      const u = [(w.b[0] - w.a[0]) / len, (w.b[1] - w.a[1]) / len];
      return { ...w, len, u, n: [-u[1], u[0]], half: (w.t * PX) / 2 };
    });
    const endAt = (i, atEnd) => {
      const w = walls[i];
      if (w.free) return atEnd ? w.b : w.a;
      const E = atEnd ? w.b : w.a, A = atEnd ? w.a : w.b;
      const dir = atEnd ? w.u : [-w.u[0], -w.u[1]];
      let trim = Infinity, ext = -Infinity;
      walls.forEach((o, j) => {
        if (j === i || o.free || segDist(E, o.a, o.b) > o.half + 0.6) return;
        const corner = Math.hypot(E[0] - o.a[0], E[1] - o.a[1]) < 0.6 || Math.hypot(E[0] - o.b[0], E[1] - o.b[1]) < 0.6;
        if (Math.abs(dot(dir, o.u)) > 0.94) {
          const sin = Math.abs(dir[0] * o.u[1] - dir[1] * o.u[0]);
          if (corner && i > j && sin > 1e-3) trim = Math.min(trim, w.len - (w.half + o.half) * sin);
          return;
        }
        let s0 = dot([A[0] - o.a[0], A[1] - o.a[1]], o.n), dn = dot(dir, o.n);
        if (s0 > 0) { s0 = -s0; dn = -dn; }
        if (corner && i < j) ext = Math.max(ext, (o.half - s0) / dn);
        else trim = Math.min(trim, (-o.half - s0) / dn);
      });
      const L = trim < Infinity ? trim : ext > -Infinity ? ext : w.len;
      return add(A, dir, L);
    };
    return walls.map((w, i) => ({ ...w, a: endAt(i, false), b: endAt(i, true) }));
  }

  const extLoop = insetLoop(EXTERIOR, EXT_IN / 2);
  const WALLS = resolveWalls([
    ...extLoop.map((a, i) => ({ a, b: extLoop[(i + 1) % extLoop.length], t: EXT_T, kind: 'ext' })),
    ...INNER_WALLS.map(([a, b, t]) => ({ a, b, t: t || INNER_T, kind: 'inner' })),
    ...GLASS_WALLS.map(([a, b]) => ({ a, b, t: GLASS_T, kind: 'glass', glass: true })),
    ...PARTITIONS.map((p) => ({ a: p.a, b: p.b, t: PART_T, kind: 'partition', room: p.room, free: true })),
  ]);

  const toSurface = ([a, b], half, kind) => {
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const u = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
    return { a, b, u, n: [-u[1], u[0]], half, kind };
  };
  const SURFACES = [
    ...WALLS.filter((w) => !w.glass).map((w) => toSurface([w.a, w.b], w.half, w.kind)),
    ...CORES.flatMap(rectEdges).map((e) => toSurface(e, 0, 'core')),
    ...rectEdges(RESTROOM).map((e) => toSurface(e, 0, 'core')),
  ];
  // 시선·이동 경로를 막는 선분(유리벽 포함)
  const BLOCKERS = [
    ...WALLS.map((w) => [w.a, w.b]),
    ...CORES.flatMap(rectEdges), ...rectEdges(RESTROOM),
  ];
  const surfacesAt = (p, u) => SURFACES.filter((s) => Math.abs(dot(u, s.u)) > 0.94 && segDist(p, s.a, s.b) <= s.half + 0.6);
  const KEEP_OUT = [RESTROOM, ...CORES];
  const inKeepOut = (p, m) => KEEP_OUT.some(([a, b, c, d]) => p[0] > a - m && p[0] < c + m && p[1] > b - m && p[1] < d + m);
  const blocked = (p, q) => BLOCKERS.some(([a, b]) => segIntersect(p, q, a, b));

  /* ---------- 작품 걸 자리 계산 ----------
   * 벽을 따라 SPACING(px) 간격으로 후보를 두고
   *  - 실제 벽면이 있고(출입구가 아니고), 액자 폭만큼 벽이 이어지며
   *  - 액자 자리에 다른 벽이 없고
   *  - 앞쪽 VIEW 거리 안에 서서 볼 수 있는 자리
   * 만 남긴다. 결과는 전시실별로 '입구에서 가까운 벽부터' 순서대로 정렬. */
  const SLOT = {
    spacing: 28,      // 작품 사이 간격(px) = 2.8 unit
    halfWidth: 9.5,   // 액자 반폭(px) — 이만큼 벽이 이어져야 함
    viewMax: 34,      // 감상 거리 최대(px)
    viewMin: 18,
    margin: 15,       // 모서리에서 띄울 거리(px)
  };

  function wallSlotsOnSegment(a, b, n, roomId, isPartition) {
    const out = [];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    const usable = len - 2 * SLOT.margin;
    if (usable < 0) return out;
    const count = Math.floor(usable / SLOT.spacing) + 1;
    const u = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
    for (let i = 0; i < count; i++) {
      const s = count === 1 ? len / 2 : SLOT.margin + (usable * i) / (count - 1);
      const p = add(a, u, s);
      const mounts = surfacesAt(p, u);
      if (!mounts.length || inKeepOut(p, 4)) continue;
      if (!surfacesAt(add(p, u, -SLOT.halfWidth - 3), u).length || !surfacesAt(add(p, u, SLOT.halfWidth + 3), u).length) continue;
      // 벽의 실내 쪽 면까지 거리
      const face = Math.max(...mounts.map((m) => {
        const dn = dot(n, m.n);
        return ((dn > 0 ? m.half : -m.half) - dot([p[0] - m.a[0], p[1] - m.a[1]], m.n)) / dn;
      }));
      const art = add(p, n, face + 0.6);
      const probe = add(p, n, face + 1.2);
      const clash = [-SLOT.halfWidth, 0, SLOT.halfWidth].some((k) => {
        const q = add(probe, u, k);
        return SURFACES.some((s) => !mounts.includes(s) && segDist(q, s.a, s.b) < s.half + 0.5);
      });
      if (clash) continue;
      // 감상 지점: 전시실 안, 시선이 막히지 않는 가장 먼 거리
      let view = null;
      for (let d = SLOT.viewMax; d >= SLOT.viewMin; d -= 2) {
        const v = add(art, n, d);
        const poly = ROOMS.find((r) => r.id === roomId).poly;
        if (!inside(v, poly) || inKeepOut(v, 6)) continue;
        if (blocked(add(art, n, 1), v)) continue;
        // 사람이 설 자리: 다른 벽에서 5px 이상
        if (SURFACES.some((s) => !mounts.includes(s) && segDist(v, s.a, s.b) < s.half + 5)) continue;
        view = { pos: v, dist: d };
        break;
      }
      if (!view) continue;
      out.push({ room: roomId, wall: p, art, n, u, view: view.pos, viewDist: view.dist, partition: !!isPartition });
    }
    return out;
  }

  function roomSlots(r) {
    let list = [];
    // 둘레 벽 (다각형 변 순서 = 한 바퀴 도는 순서)
    edgesOf(r.poly).forEach(([a, b], ei) => {
      const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const u = [(b[0] - a[0]) / len, (b[1] - a[1]) / len];
      let n = [-u[1], u[0]];
      if (!inside(add([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], n, 8), r.poly)) n = [-n[0], -n[1]];
      wallSlotsOnSegment(a, b, n, r.id, false).forEach((s) => list.push({ ...s, edge: ei }));
    });
    // 입구에서 가장 가까운 자리부터 시작하도록 순서를 돌림
    if (list.length) {
      let k = 0, best = Infinity;
      list.forEach((s, i) => {
        const d = Math.hypot(s.view[0] - r.entry[0], s.view[1] - r.entry[1]);
        if (d < best) { best = d; k = i; }
      });
      list = list.slice(k).concat(list.slice(0, k));
    }
    // 가벽 양면
    PARTITIONS.filter((p) => p.room === r.id).forEach((p) => {
      const len = Math.hypot(p.b[0] - p.a[0], p.b[1] - p.a[1]);
      const u = [(p.b[0] - p.a[0]) / len, (p.b[1] - p.a[1]) / len];
      const n1 = [-u[1], u[0]], n2 = [u[1], -u[0]];
      list.push(...wallSlotsOnSegment(p.a, p.b, n1, r.id, true));
      list.push(...wallSlotsOnSegment(p.b, p.a, n2, r.id, true));
    });
    list.forEach((s, i) => { s.index = i; s.spacing = SLOT.spacing; });
    return list;
  }

  // needs: { 전시실id: 걸어야 할 작품 수 }. 넉넉하면 간격 28px, 모자라면 간격을 줄여(최소 21px) 자리를 늘린다.
  function computeSlots(needs) {
    const result = {};
    const base = SLOT.spacing;
    ROOMS.forEach((r) => {
      const need = needs && needs[r.id] || 0;
      let list = [];
      for (let sp = base; sp >= 21; sp -= 1) {
        SLOT.spacing = sp;
        list = roomSlots(r);
        if (list.length >= need) break;
      }
      SLOT.spacing = base;
      result[r.id] = list;
    });
    return result;
  }

  const FloorPlan = {
    PX, CX, CY, WALL_H, FLOOR_T, PLINTH, P,
    ROOMS, EXTERIOR, COURT, LOBBY, CORRIDOR, PASSAGE, CORES, RESTROOM, ARC,
    WALLS, SURFACES, BLOCKERS, PARTITIONS, SLOT,
    inside, segDist, add, dot, edgesOf, rectEdges, centroid, blocked, inKeepOut,
    computeSlots,
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = FloorPlan;
  else root.FloorPlan = FloorPlan;
})(typeof window !== 'undefined' ? window : globalThis);
