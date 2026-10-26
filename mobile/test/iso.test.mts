// Checks for src/iso.ts. Run: npm test  (node >= 22.18 strips the types).
import {
  boxFaces, boxShadow, camera, headingYaw, cityBoxes, depth, distToLine, fitSection, followScale, hull, lineU, linePoints, noiseColor,
  noiseT, project,
} from '../src/iso.ts';

let failed = 0;
function check(name: string, ok: boolean, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`);
  if (!ok) failed++;
}
const L = 2270;
const c = camera(0.5);

// projection: forward along the line goes up the screen, height goes up, and the origin stays put
{
  const o = project(c, 0, 0), f = project(c, 0, 100), up = project(c, 0, 0, 50);
  check('origin maps to (0, 0)', o.x === 0 && Math.abs(o.y) < 1e-9);
  check('forward along the line goes up the screen', f.y < o.y);
  check('height goes up the screen', up.y < o.y && up.x === o.x);
  check('further along the line is deeper', depth(c, 0, 100) > depth(c, 0, 0));
}

// the line starts and ends on the axis, the station ends are fixed
check('line starts and ends at u = 0', Math.abs(lineU(0, L)) < 1e-9 && Math.abs(lineU(L, L)) < 1e-6);
check('line points span the section', (() => { const p = linePoints(L); return p[0].v === 0 && p[p.length - 1].v === L; })());

// city: clear of the line, painted far to near, visible faces are the ones nearer the viewer
{
  const boxes = cityBoxes(L, c);
  check('enough generated blocks', boxes.length > 150 && boxes.length < 900, `${boxes.length}`);
  check('blocks keep clear of the line', boxes.every((b) => distToLine(b.u, b.v, L) >= 46));
  check('blocks are sorted far to near', boxes.every((b, i) => i === 0 || boxes[i - 1].d >= b.d));
  const b = { u: 200, v: 300, a: 15, b: 15, h: 40, d: 0 };
  const f = boxFaces(c, b);
  const mean = (pts: { x: number; y: number }[]) => pts.reduce((s, p) => s + p.y, 0) / pts.length;
  check('top face sits above the side faces', mean(f.top) < mean(f.sideA) && mean(f.top) < mean(f.sideB));
  // the visible v face is the near one (-v when the camera looks along +v)
  const nearV = project(c, b.u, b.v - b.b, 0).y, farV = project(c, b.u, b.v + b.b, 0).y;
  check('visible v face is the near one', Math.abs(f.sideB[0].y - project(c, b.u - b.a, b.v - b.b, 0).y) < 1e-9 && nearV > farV);
  check('shadow is a closed hull', boxShadow(c, b).length >= 4);
}
check('hull of a square with a point inside is the square', hull([{ x: 0, y: 0 }, { x: 1, y: 0 }, { x: 1, y: 1 }, { x: 0, y: 1 }, { x: 0.5, y: 0.5 }]).length === 4);

// framing: the whole section fits the rectangle
{
  const r = { left: 20, top: 200, right: 370, bottom: 560 };
  const { k, dx, dy } = fitSection(L, r);
  const cam = camera(k);
  const inside = linePoints(L).every((p) => {
    const q = project(cam, p.u, p.v);
    return q.x + dx >= r.left - 0.5 && q.x + dx <= r.right + 0.5 && q.y + dy >= r.top - 0.5 && q.y + dy <= r.bottom + 0.5;
  });
  check('whole section fits the rectangle', inside && k > 0, `k=${k.toFixed(3)}`);
  check('follow view is closer than the whole section', followScale(r) > k);
}

// heading-up: with the camera turned to headingYaw (no offset), the line points straight up the screen
{
  for (const sNow of [100, 800, 1500, 2200]) {
    const yaw = headingYaw(sNow, L, 0, 0.0001);
    const cc = camera(1, yaw);
    const p0 = project(cc, lineU(sNow, L), sNow), p1 = project(cc, lineU(sNow + 5, L), sNow + 5);
    if (!(Math.abs(p1.x - p0.x) < 0.05 && p1.y < p0.y)) { check(`heading-up at ${sNow} m`, false, `${p1.x - p0.x}`); }
  }
  check('heading-up turns the line to point up the screen', true);
  check('heading yaw is rounded to the step', headingYaw(900, L) % 4 === 0);
}

// noise colours
check('quiet end is lilac', noiseColor(0) === '#c9c6f7');
check('loud end is amber', noiseColor(1) === '#ffc53d');
check('out of range and NaN are clamped', noiseColor(5) === '#ffc53d' && noiseColor(NaN) === '#c9c6f7');
check('calibrated 80 dB(A) is mid-scale', Math.abs(noiseT(80, true) - 0.5) < 1e-9);
check('dBFS -30 is mid-scale', Math.abs(noiseT(-30, false) - 0.5) < 1e-9);

if (failed) { console.log(`\n${failed} check(s) failed`); process.exit(1); }
console.log('\nall iso checks passed');
