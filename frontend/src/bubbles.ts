/**
 * Circle packing — a dependency-free port of d3-hierarchy's packSiblings
 * (Wang 2006 front-chain), with an approximate enclosing circle. Enough to
 * draw a nested bubble chart (rooms packed into a cluster, each holding one
 * bubble per sensor) without pulling d3 into the bundle.
 */
export type C = { x: number; y: number; r: number };

function place(a: C, b: C, c: C) {
  const dx = b.x - a.x, dy = b.y - a.y, d2 = dx * dx + dy * dy;
  if (d2) {
    const a2 = (a.r + c.r) ** 2, b2 = (b.r + c.r) ** 2;
    if (a2 > b2) {
      const x = (d2 + b2 - a2) / (2 * d2);
      const y = Math.sqrt(Math.max(0, b2 / d2 - x * x));
      c.x = b.x - x * dx - y * dy;
      c.y = b.y - x * dy + y * dx;
    } else {
      const x = (d2 + a2 - b2) / (2 * d2);
      const y = Math.sqrt(Math.max(0, a2 / d2 - x * x));
      c.x = a.x + x * dx - y * dy;
      c.y = a.y + x * dy + y * dx;
    }
  } else {
    c.x = a.x + c.r;
    c.y = a.y;
  }
}

function intersects(a: C, b: C) {
  const dr = a.r + b.r - 1e-6, dx = b.x - a.x, dy = b.y - a.y;
  return dr > 0 && dr * dr > dx * dx + dy * dy;
}

type N = { _: C; next: N; previous: N };

function score(n: N) {
  const a = n._, b = n.next._, ab = a.r + b.r;
  const dx = (a.x * b.r + b.x * a.r) / ab;
  const dy = (a.y * b.r + b.y * a.r) / ab;
  return dx * dx + dy * dy;
}

/** Lay out circles (radii preset) so none overlap, packed near the origin.
 *  Mutates each circle's x/y in place; returns the same array. */
export function packSiblings(circles: C[]): C[] {
  const n = circles.length;
  if (n === 0) return circles;
  circles[0].x = 0; circles[0].y = 0;
  if (n === 1) return circles;
  circles[0].x = -circles[1].r; circles[1].x = circles[0].r; circles[1].y = 0;
  if (n === 2) return circles;
  place(circles[1], circles[0], circles[2]);

  let A: N = { _: circles[0] } as N;
  let B: N = { _: circles[1] } as N;
  const C0: N = { _: circles[2] } as N;
  A.next = B; A.previous = C0;
  B.next = C0; B.previous = A;
  C0.next = A; C0.previous = B;

  pack: for (let i = 3; i < n; ++i) {
    const c = circles[i];
    place(A._, B._, c);
    const Cn: N = { _: c } as N;

    let j = B.next, k = A.previous, sj = B._.r, sk = A._.r;
    do {
      if (sj <= sk) {
        if (intersects(j._, c)) { B = j; A.next = B; B.previous = A; --i; continue pack; }
        sj += j._.r; j = j.next;
      } else {
        if (intersects(k._, c)) { A = k; A.next = B; B.previous = A; --i; continue pack; }
        sk += k._.r; k = k.previous;
      }
    } while (j !== k.next);

    Cn.previous = A; Cn.next = B;
    A.next = Cn; B.previous = Cn; B = Cn;

    let aa = score(A);
    let cc: N = Cn;
    while ((cc = cc.next) !== B) {
      const ca = score(cc);
      if (ca < aa) { A = cc; aa = ca; }
    }
    B = A.next;
  }
  return circles;
}

/** Approximate minimum enclosing circle: centroid + furthest reach. Not the
 *  true minimal circle, but visually tight enough for a dashboard bubble. */
export function enclose(circles: C[]): C {
  if (!circles.length) return { x: 0, y: 0, r: 0 };
  let cx = 0, cy = 0;
  for (const c of circles) { cx += c.x; cy += c.y; }
  cx /= circles.length; cy /= circles.length;
  let r = 0;
  for (const c of circles) {
    const d = Math.hypot(c.x - cx, c.y - cy) + c.r;
    if (d > r) r = d;
  }
  return { x: cx, y: cy, r };
}
