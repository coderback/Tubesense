// Checks for src/route.ts. Run: npm test  (node >= 22.18 strips the types).
import { SECTIONS, TimeGuess, cityBlocks, nextSection, parseServerPosition, reverseSection, routePoint } from '../src/route.ts';

let failed = 0;
function check(name: string, ok: boolean, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  ' + detail : ''}`);
  if (!ok) failed++;
}
const LB = SECTIONS[0];

// time guess: nothing before departure, proportional while moving, holds while stopped mid-section, arrives late
{
  const g = new TimeGuess(LB);
  g.step('stopped');
  check('no position before the first moving second', g.position() === null);
  for (let i = 0; i < 71; i++) g.step('moving');
  const p = g.position();
  check('half the usual run time is half the section', !!p && Math.abs(p.sM - LB.lengthM * 71 / 142) < 1e-6, `${p?.sM}`);
  check('time guess is labelled as such', p?.source === 'time');
  g.step('stopped');
  check('a stop early in the section is not arrival', !g.arrived);
  for (let i = 0; i < 20; i++) g.step('moving');
  g.step('stopped');
  check('a stop after 60 % of the run is arrival', g.arrived && g.position()?.sM === LB.lengthM);
  check('null state is ignored', (g.step(null), g.arrived));
}

// server position: accepted only when complete and for the same section
{
  check('missing position -> null', parseServerPosition({ ok: true }, LB) === null);
  check('bad numbers -> null', parseServerPosition({ position: { s_m: 'x' } }, LB) === null);
  const p = parseServerPosition({ position: { s_m: 881, length_m: 2270, band_lo: 849, band_hi: 881, section: 'EB LST->BTH' } }, LB);
  check('valid position parsed', !!p && p.sM === 881 && p.lo === 849 && p.source === 'tracker');
  check('other section -> null', parseServerPosition({ position: { s_m: 10, section: 'EB BTH->MLE' } }, LB) === null);
  check('clamped to the section', parseServerPosition({ position: { s_m: 9999, length_m: 2270 } }, LB)?.sM === 2270);
}

// section helpers and drawing
check('next eastbound section after LST->BTH is BTH->MLE', nextSection(LB).key === 'EB BTH->MLE');
check('reverse of LST->BTH is BTH->LST', reverseSection(LB).key === 'WB BTH->LST');
{
  const a = routePoint(0, 390, 100, 700), b = routePoint(1, 390, 100, 700);
  check('route starts at the bottom and ends at the top', a.y === 700 && b.y === 100);
  const blocks = cityBlocks(390, 800, 100, 700);
  const clear = blocks.every((bk) => {
    const t = Math.min(1, Math.max(0, (700 - bk.y) / 600));
    const rx = routePoint(t, 390, 100, 700).x;
    return !(bk.x < rx + 30 && bk.x + bk.w > rx - 30);
  });
  check('generated blocks keep clear of the line', blocks.length > 20 && clear, `${blocks.length} blocks`);
  check('blocks are sorted back to front', blocks.every((bk, i) => i === 0 || blocks[i - 1].y <= bk.y));
}

if (failed) { console.log(`\n${failed} check(s) failed`); process.exit(1); }
console.log('\nall route checks passed');
