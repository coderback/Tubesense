// Heading-up map of the current section (design v2): generated city blocks in an oblique 3D view, the line from the start
// station (bottom) to the end station (top), the travelled part highlighted, and the estimate with its range.
// Schematic only: the buildings are generated and the line is not the real track geometry.
import { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, G, Polyline, Rect } from 'react-native-svg';

import { STATIONS, cityBlocks, fmtM, routePoint, routePolyline, type Position, type Section } from './route';

export type MapTheme = {
  ground: string; top: string; front: string; route: string; routeDone: string; glow: string; ring: string; ringFill: string;
  pillBg: string; pillInk: string; tagBg: string; tagInk: string; tagSub: string;
};
export const LIGHT_MAP: MapTheme = {
  ground: '#EEF1F8', top: '#FFFFFF', front: '#D9DFEC', route: '#C9CEE6', routeDone: '#3D5AFE', glow: 'rgba(61,90,254,0.16)',
  ring: '#121735', ringFill: '#FFFFFF', pillBg: '#121735', pillInk: '#FFFFFF', tagBg: '#FFFFFF', tagInk: '#121735', tagSub: '#4B5275',
};
export const TUNNEL_MAP: MapTheme = {
  ground: '#03050C', top: '#1A2246', front: '#0D1330', route: '#2A3459', routeDone: '#5B7BFF', glow: 'rgba(91,123,255,0.25)',
  ring: '#DCE3FF', ringFill: '#0B1022', pillBg: '#EEF1FB', pillInk: '#070A16', tagBg: '#EEF1FB', tagInk: '#070A16', tagSub: '#4B5275',
};

type Props = { width: number; height: number; section: Section; pos: Position | null; theme: MapTheme; topInset: number; bottomInset: number };

export function RideMap({ width, height, section, pos, theme, topInset, bottomInset }: Props) {
  const top = topInset + 40;
  const bottom = Math.max(top + 120, height - bottomInset - 40);
  const blocks = useMemo(() => cityBlocks(width, height, top, bottom), [width, height, top, bottom]);
  const t = pos ? Math.min(1, Math.max(0, pos.sM / pos.lengthM)) : 0;
  const dot = routePoint(t, width, top, bottom);
  const a = routePoint(0, width, top, bottom);
  const b = routePoint(1, width, top, bottom);
  const lo = pos?.lo != null ? Math.max(0, pos.lo / pos.lengthM) : null;
  const hi = pos?.hi != null ? Math.min(1, pos.hi / pos.lengthM) : null;

  return (
    <View style={{ width, height, backgroundColor: theme.ground }} accessible accessibilityLabel={pos
      ? `Map: about ${fmtM(pos.sM)} from ${STATIONS[section.from].name} towards ${STATIONS[section.to].name}, ${pos.source === 'tracker' ? 'tracker estimate' : 'rough guess from time'}`
      : `Map of ${STATIONS[section.from].name} to ${STATIONS[section.to].name}`}>
      <Svg width={width} height={height}>
        {blocks.map((bk, i) => (
          <G key={i}>
            <Rect x={bk.x} y={bk.y - bk.h} width={bk.w} height={bk.h} fill={theme.front} />
            <Rect x={bk.x} y={bk.y - bk.h - bk.d * 0.55} width={bk.w} height={bk.d * 0.55} fill={theme.top} />
          </G>
        ))}
        <Polyline points={routePolyline(width, top, bottom)} fill="none" stroke={theme.glow} strokeWidth={30} strokeLinecap="round" strokeLinejoin="round" />
        <Polyline points={routePolyline(width, top, bottom)} fill="none" stroke={theme.route} strokeWidth={10} strokeLinecap="round" strokeLinejoin="round" />
        {pos && t > 0.001 && (
          <Polyline points={routePolyline(width, top, bottom, 0, t)} fill="none" stroke={theme.routeDone} strokeWidth={10} strokeLinecap="round" strokeLinejoin="round" />
        )}
        {pos && lo !== null && hi !== null && hi - lo > 0.004 && (
          <Polyline points={routePolyline(width, top, bottom, lo, hi, 16)} fill="none" stroke={theme.routeDone} strokeOpacity={0.3} strokeWidth={26} strokeLinecap="round" />
        )}
        <Circle cx={a.x} cy={a.y} r={10} fill={theme.ringFill} stroke={theme.ring} strokeWidth={4} />
        <Circle cx={b.x} cy={b.y} r={10} fill={theme.ringFill} stroke={theme.ring} strokeWidth={4} />
        {pos && (
          <G>
            <Circle cx={dot.x} cy={dot.y} r={26} fill={theme.routeDone} fillOpacity={0.16} />
            <Circle cx={dot.x} cy={dot.y} r={13} fill={theme.routeDone} stroke="#FFFFFF" strokeWidth={4} />
          </G>
        )}
      </Svg>
      <View style={[s.pill, { left: Math.min(width - 170, a.x + 16), top: a.y - 15, backgroundColor: theme.pillBg }]}>
        <View style={s.roundel}><Text style={s.roundelText}>C</Text></View>
        <Text style={[s.pillText, { color: theme.pillInk }]}>{STATIONS[section.from].short}</Text>
      </View>
      <View style={[s.pill, { left: Math.min(width - 170, b.x + 16), top: b.y - 15, backgroundColor: theme.pillBg }]}>
        <View style={s.roundel}><Text style={s.roundelText}>C</Text></View>
        <Text style={[s.pillText, { color: theme.pillInk }]}>{STATIONS[section.to].short}</Text>
      </View>
      {pos && (
        <View style={[s.tag, { left: Math.max(8, Math.min(width - 160, dot.x - 76)), top: dot.y + 30, backgroundColor: theme.tagBg }]}>
          <Text style={[s.tagMain, { color: theme.tagInk }]}>You · {fmtM(pos.sM)}</Text>
          <Text style={[s.tagSub, { color: theme.tagSub }]}>{pos.source === 'tracker' ? 'tracker estimate, no GPS' : 'rough guess from time, no GPS'}</Text>
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  pill: { position: 'absolute', flexDirection: 'row', alignItems: 'center', gap: 6, height: 30, paddingLeft: 3, paddingRight: 12, borderRadius: 15 },
  roundel: { width: 24, height: 24, borderRadius: 12, backgroundColor: '#E1251B', alignItems: 'center', justifyContent: 'center' },
  roundelText: { color: '#FFFFFF', fontSize: 11, fontWeight: '800' },
  pillText: { fontSize: 13, fontWeight: '700' },
  tag: { position: 'absolute', width: 152, alignItems: 'center', paddingVertical: 7, borderRadius: 14,
    shadowColor: '#28326E', shadowOpacity: 0.3, shadowRadius: 14, shadowOffset: { width: 0, height: 8 }, elevation: 6 },
  tagMain: { fontSize: 15, fontWeight: '800', fontVariant: ['tabular-nums'] },
  tagSub: { fontSize: 11, fontWeight: '600' },
});
