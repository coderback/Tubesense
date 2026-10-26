// Isometric 3D map of the current section (design v2): generated city blocks with shadows, the Central line with a glow,
// the travelled part coloured by the sound measured on this ride, the stations and the position with its range.
// Same camera as the web app's three.js map; drawn with react-native-svg because Expo Go has no WebGL view.
// Schematic only: the buildings are generated and the line is not the real track geometry.
import { memo, useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Ellipse, G, Path, Polyline } from 'react-native-svg';

import {
  boxFaces, boxShadow, camera, cityBoxes, fitSection, followScale, headingYaw, lineU, linePoints, noiseColor, noiseT, polyPath, project,
  ptsString, type Cam, type Rect,
} from './iso';
import { STATIONS, fmtM, type Position, type Section } from './route';

export type MapTheme = {
  ground: string; shadow: string; top: string; sideA: string; sideB: string; casing: string; route: string; glow: string;
  done: string; ring: string; ringFill: string; pillBg: string; pillInk: string; tagBg: string; tagInk: string; tagSub: string;
};
export const LIGHT_MAP: MapTheme = {
  ground: '#E9EDF6', shadow: 'rgba(40,50,110,0.10)', top: '#FFFFFF', sideA: '#DCE2F0', sideB: '#C8D0E4', casing: '#FFFFFF',
  route: '#C9C6F7', glow: 'rgba(61,90,254,0.14)', done: '#3D5AFE', ring: '#121735', ringFill: '#FFFFFF',
  pillBg: '#121735', pillInk: '#FFFFFF', tagBg: '#FFFFFF', tagInk: '#121735', tagSub: '#646B8E',
};
export const TUNNEL_MAP: MapTheme = {
  ground: '#03050C', shadow: 'rgba(0,0,0,0.55)', top: '#1B2348', sideA: '#111834', sideB: '#0B1128', casing: '#0B1022',
  route: '#2E3763', glow: 'rgba(91,123,255,0.26)', done: '#5B7BFF', ring: '#DCE3FF', ringFill: '#0B1022',
  pillBg: '#EEF1FB', pillInk: '#070A16', tagBg: '#EEF1FB', tagInk: '#070A16', tagSub: '#4B5275',
};

export type NoiseSample = { sM: number; db: number };

type Props = {
  width: number;
  height: number;
  section: Section;
  pos: Position | null;
  theme: MapTheme;
  /** 'follow' keeps the position at `anchor` and shows ~760 m of line; 'section' fits the whole section into `frame`. */
  mode: 'follow' | 'section';
  frame: Rect;
  anchor?: { x: number; y: number };
  /** sound along the way, painted onto the travelled line */
  samples?: NoiseSample[];
  calibrated?: boolean;
  /** second line of the tag under the dot; null hides the tag */
  tagSub?: string | null;
};

export function RideMap({ width, height, section, pos, theme, mode, frame, anchor, samples = [], calibrated = false, tagSub }: Props) {
  const L = section.lengthM;
  const fit = useMemo(() => fitSection(L, frame), [L, frame.left, frame.top, frame.right, frame.bottom]); // eslint-disable-line react-hooks/exhaustive-deps
  const k = mode === 'follow' ? followScale(frame) : fit.k;
  const sNow = pos ? Math.min(L, Math.max(0, pos.sM)) : 0;
  // following: heading-up (the line ahead points up the screen); whole section: the fixed camera of the web map
  const yaw = mode === 'follow' ? headingYaw(sNow, L) : undefined;
  const cam = useMemo(() => camera(k, yaw), [k, yaw]);
  const dotW = project(cam, lineU(sNow, L), sNow);
  const at = anchor ?? { x: width / 2, y: (frame.top + frame.bottom) / 2 };
  const dx = mode === 'follow' ? at.x - dotW.x : fit.dx;
  const dy = mode === 'follow' ? at.y - dotW.y : fit.dy;

  const P = (s: number) => project(cam, lineU(s, L), s);
  const lineAll = useMemo(() => ptsString(linePoints(L, 0, L, 80).map((p) => project(cam, p.u, p.v))), [cam, L]);
  const tail = useMemo(() => {
    // the line runs on past both stations so the map does not end at the platform
    const a = ptsString(linePoints(L, -420, 0, 6).map((p) => project(cam, 0, p.v)));
    const b = ptsString(linePoints(L, L, L + 420, 6).map((p) => project(cam, 0, p.v)));
    return [a, b];
  }, [cam, L]);

  // travelled part: one segment per second of sound, coloured on the noise scale; plain accent when there is no sound
  const painted = useMemo(() => {
    const segs: { pts: string; color: string }[] = [];
    let prev = 0;
    for (const sm of samples) {
      const s1 = Math.min(L, Math.max(prev, sm.sM));
      if (s1 - prev > 0.5) {
        segs.push({ pts: ptsString(linePoints(L, prev, s1, 3).map((p) => project(cam, p.u, p.v))), color: noiseColor(noiseT(sm.db, calibrated)) });
        prev = s1;
      }
    }
    return { segs, end: prev };
  }, [samples, cam, L, calibrated]);
  const plainDone = pos && sNow > painted.end + 1 ? ptsString(linePoints(L, painted.end, sNow, 24).map((p) => project(cam, p.u, p.v))) : null;
  const band = pos && pos.lo != null && pos.hi != null && pos.hi - pos.lo > 2
    ? ptsString(linePoints(L, Math.max(0, pos.lo), Math.min(L, pos.hi), 16).map((p) => project(cam, p.u, p.v)))
    : null;

  const a = P(0), b = P(L);
  const scr = (p: { x: number; y: number }) => ({ x: p.x + dx, y: p.y + dy });
  const sa = scr(a), sb = scr(b), sd = scr(dotW);
  const ringR = Math.max(9, Math.min(16, 34 * k));
  const visible = (p: { x: number; y: number }) => p.x > -60 && p.x < width + 60 && p.y > -40 && p.y < height + 40;

  return (
    <View
      style={{ width, height, backgroundColor: theme.ground }}
      accessible
      accessibilityLabel={pos
        ? `Map: about ${fmtM(pos.sM)} from ${STATIONS[section.from].name} towards ${STATIONS[section.to].name}, ${pos.source === 'tracker' ? 'laptop tracker estimate' : 'rough guess from time'}`
        : `Map of ${STATIONS[section.from].name} to ${STATIONS[section.to].name}`}
    >
      <Svg width={width} height={height}>
        <G transform={`translate(${dx.toFixed(1)} ${dy.toFixed(1)})`}>
          <City L={L} cam={cam} theme={theme} />
          {tail.map((t, i) => (
            <Polyline key={i} points={t} fill="none" stroke={theme.route} strokeOpacity={0.5} strokeWidth={6} strokeLinecap="round" strokeDasharray="2 10" />
          ))}
          <Polyline points={lineAll} fill="none" stroke={theme.glow} strokeWidth={30} strokeLinecap="round" strokeLinejoin="round" />
          <Polyline points={lineAll} fill="none" stroke={theme.casing} strokeWidth={14} strokeLinecap="round" strokeLinejoin="round" />
          <Polyline points={lineAll} fill="none" stroke={theme.route} strokeWidth={8} strokeLinecap="round" strokeLinejoin="round" />
          {painted.segs.map((sg, i) => (
            <Polyline key={i} points={sg.pts} fill="none" stroke={sg.color} strokeWidth={8} strokeLinecap="round" strokeLinejoin="round" />
          ))}
          {plainDone && <Polyline points={plainDone} fill="none" stroke={theme.done} strokeWidth={8} strokeLinecap="round" strokeLinejoin="round" />}
          {band && <Polyline points={band} fill="none" stroke={theme.done} strokeOpacity={0.28} strokeWidth={24} strokeLinecap="round" />}
          {[a, b].map((p, i) => (
            <G key={i}>
              <Ellipse cx={p.x} cy={p.y} rx={ringR * 1.9} ry={ringR * 1.9 * cam.sinE} fill={theme.done} fillOpacity={0.1} />
              <Ellipse cx={p.x} cy={p.y} rx={ringR} ry={ringR * cam.sinE} fill={theme.ringFill} stroke={theme.ring} strokeWidth={3} />
            </G>
          ))}
          {pos && (
            <G>
              <Ellipse cx={dotW.x} cy={dotW.y} rx={30} ry={30 * cam.sinE} fill={theme.done} fillOpacity={0.18} />
              <Ellipse cx={dotW.x} cy={dotW.y + 3} rx={14} ry={14 * cam.sinE} fill="#000000" fillOpacity={0.18} />
              <Path d={`M${dotW.x - 13} ${dotW.y - 6}a13 13 0 1 0 26 0a13 13 0 1 0 -26 0z`} fill={theme.done} stroke="#FFFFFF" strokeWidth={4} />
            </G>
          )}
        </G>
      </Svg>
      {visible(sa) && <StationPill x={sa.x} y={sa.y} name={STATIONS[section.from].short} theme={theme} width={width} />}
      {visible(sb) && <StationPill x={sb.x} y={sb.y} name={STATIONS[section.to].short} theme={theme} width={width} />}
      {pos && tagSub !== null && visible(sd) && (
        <View style={[s.tag, { left: Math.max(8, Math.min(width - 188, sd.x - 90)), top: sd.y + 22, backgroundColor: theme.tagBg }]}>
          <Text style={[s.tagMain, { color: theme.tagInk }]}>You · {fmtM(pos.sM)}</Text>
          <Text style={[s.tagSub, { color: theme.tagSub }]}>{tagSub ?? (pos.source === 'tracker' ? 'laptop estimate, no GPS' : 'rough guess, no GPS')}</Text>
        </View>
      )}
    </View>
  );
}

function StationPill({ x, y, name, theme, width }: { x: number; y: number; name: string; theme: MapTheme; width: number }) {
  return (
    <View style={[s.pill, { left: Math.max(8, Math.min(width - 160, x + 20)), top: y - 16, backgroundColor: theme.pillBg }]}>
      <View style={s.roundel}>
        <Text style={s.roundelText}>C</Text>
      </View>
      <Text style={[s.pillText, { color: theme.pillInk }]} numberOfLines={1}>{name}</Text>
    </View>
  );
}

// the city only changes with the scale or the theme, so following the train just moves the group above
const City = memo(function City({ L, cam, theme }: { L: number; cam: Cam; theme: MapTheme }) {
  const { shadows, boxes } = useMemo(() => {
    const list = cityBoxes(L, cam);
    return {
      shadows: list.map((bx) => polyPath(boxShadow(cam, bx))).join(''),
      boxes: list.map((bx) => {
        const f = boxFaces(cam, bx);
        return { a: polyPath(f.sideA), b: polyPath(f.sideB), t: polyPath(f.top) };
      }),
    };
  }, [L, cam]);
  return (
    <G>
      <Path d={shadows} fill={theme.shadow} />
      {boxes.map((bx, i) => (
        <G key={i}>
          <Path d={bx.a} fill={theme.sideA} />
          <Path d={bx.b} fill={theme.sideB} />
          <Path d={bx.t} fill={theme.top} />
        </G>
      ))}
    </G>
  );
});

const s = StyleSheet.create({
  pill: { position: 'absolute', flexDirection: 'row', alignItems: 'center', gap: 7, height: 32, paddingLeft: 4, paddingRight: 13, borderRadius: 16,
    shadowColor: '#28326E', shadowOpacity: 0.25, shadowRadius: 10, shadowOffset: { width: 0, height: 6 }, elevation: 4 },
  roundel: { width: 24, height: 24, borderRadius: 12, backgroundColor: '#E1251B', alignItems: 'center', justifyContent: 'center' },
  roundelText: { color: '#FFFFFF', fontSize: 11, fontFamily: 'PlusJakartaSans_800ExtraBold' },
  pillText: { fontSize: 13, fontFamily: 'PlusJakartaSans_700Bold' },
  tag: { position: 'absolute', width: 180, alignItems: 'center', paddingVertical: 8, borderRadius: 16,
    shadowColor: '#28326E', shadowOpacity: 0.3, shadowRadius: 14, shadowOffset: { width: 0, height: 8 }, elevation: 6 },
  tagMain: { fontSize: 15, fontFamily: 'PlusJakartaSans_800ExtraBold', fontVariant: ['tabular-nums'] },
  tagSub: { fontSize: 11, fontFamily: 'PlusJakartaSans_600SemiBold' },
});
