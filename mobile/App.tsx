import AsyncStorage from '@react-native-async-storage/async-storage';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { Barometer, DeviceMotion, Magnetometer } from 'expo-sensors';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  useWindowDimensions,
  type LayoutChangeEvent,
} from 'react-native';
import Svg, { Circle, Line, Path, Polyline } from 'react-native-svg';

import { LIGHT_MAP, RideMap, TUNNEL_MAP } from './src/RideMap';
import {
  SECTIONS,
  STATIONS,
  TimeGuess,
  fmtClock,
  fmtM,
  nextSection,
  parseServerPosition,
  reverseSection,
  type Position,
  type Section,
} from './src/route';

import {
  KnockDetector,
  StopDetector,
  enqueue,
  ingestUrl,
  magnitudeMean,
  pressureChange,
  rotationVector,
  round,
  sessionId,
  summariseMotion,
  type MotionSample,
  type Vec3,
} from './src/metrics';

// Design v2 tokens, shared with webapp/static/css/app.css: a light "surface" look and a dark "tunnel" look.
type Palette = {
  bg: string; sheet: string; surface: string; surface2: string; line: string; ink: string; ink2: string; ink3: string;
  accent: string; onAccent: string; amber: string; onAmber: string; green: string; red: string; redSoft: string;
  card: string; cardInk: string; cardSub: string;
};
const LIGHT: Palette = {
  bg: '#EEF1F8', sheet: '#FFFFFF', surface: '#FFFFFF', surface2: '#F4F5FB', line: '#E3E6F0', ink: '#121735', ink2: '#4B5275', ink3: '#646B8E',
  accent: '#3D5AFE', onAccent: '#FFFFFF', amber: '#FFB020', onAmber: '#1F1203', green: '#12A27A', red: '#C2333A', redSoft: '#FDECEC',
  card: '#121735', cardInk: '#FFFFFF', cardSub: '#C3CAE8',
};
const TUNNEL: Palette = {
  bg: '#03050C', sheet: '#0B1022', surface: '#0E1328', surface2: '#151B36', line: '#1E2649', ink: '#EEF1FB', ink2: '#A9B4DA', ink3: '#8A94BC',
  accent: '#5B7BFF', onAccent: '#FFFFFF', amber: '#FFC53D', onAmber: '#1F1203', green: '#34D399', red: '#FF7A80', redSoft: '#2A1420',
  card: '#000000', cardInk: '#FFFFFF', cardSub: '#A9B4DA',
};

const HOST_KEY = 'tubesense.laptopHost';
const SECTION_KEY = 'tubesense.section';
const TUNNEL_KEY = 'tubesense.tunnel';
const DEFAULT_HOST = '172.20.10.3';
const MOTION_MS = 10; // DeviceMotion: iOS caps at ~100 Hz
const MAG_MS = 50;
const BARO_MS = 200; // iOS delivers ~1 Hz regardless
const OUTBOX_MAX = 60;
const POST_TIMEOUT_MS = 2500;
const SPARK_S = 60;

type Row = {
  source: 'tubesense-mobile';
  session: string;
  t: number;
  ts: string;
  yaw_rate_dps: number;
  acc_h_rms: number;
  vib_rms: number;
  b_abs_ut: number | null;
  pressure_hpa: number | null;
  d_pressure_10s: number | null;
  state: 'moving' | 'stopped';
  knock: boolean;
  rates: { motion: number; mag: number; baro: number };
};

type Sub = { remove: () => void };

export default function App() {
  const [host, setHost] = useState(DEFAULT_HOST);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [row, setRow] = useState<Row | null>(null);
  const [yawHist, setYawHist] = useState<number[]>([]);
  const [knockAt, setKnockAt] = useState(0);
  const [net, setNet] = useState({ sent: 0, failed: 0, queued: 0, last: '' as string });
  const [sparkW, setSparkW] = useState(300);
  const [, setNow] = useState(0);
  const [section, setSection] = useState<Section>(SECTIONS[0]);
  const [tunnel, setTunnel] = useState(false);
  const [guessPos, setGuessPos] = useState<Position | null>(null);
  const [serverPos, setServerPos] = useState<Position | null>(null);
  const [details, setDetails] = useState(false);

  const r = useRef({
    subs: [] as Sub[],
    timer: null as ReturnType<typeof setInterval> | null,
    session: '',
    t: 0,
    motion: [] as MotionSample[],
    mag: [] as Vec3[],
    baroN: 0,
    baro: [] as { t: number; p: number }[],
    lastSampleT: 0,
    knocks: new KnockDetector(),
    knockFlag: false,
    stops: new StopDetector(),
    hState: { h: [0, 0, 0] as Vec3, t: null as number | null },
    outbox: [] as Row[],
    inFlight: false,
    host: DEFAULT_HOST,
    section: SECTIONS[0] as Section,
    guess: new TimeGuess(SECTIONS[0]),
  });

  useEffect(() => {
    AsyncStorage.getItem(HOST_KEY)
      .then((h) => {
        if (h) {
          setHost(h);
          r.current.host = h;
        }
      })
      .catch(() => {});
    AsyncStorage.getItem(SECTION_KEY)
      .then((k) => {
        const sec = SECTIONS.find((x) => x.key === k);
        if (sec) {
          setSection(sec);
          r.current.section = sec;
        }
      })
      .catch(() => {});
    AsyncStorage.getItem(TUNNEL_KEY)
      .then((v) => setTunnel(v === '1'))
      .catch(() => {});
    return () => stopCapture();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const onHost = (h: string) => {
    setHost(h);
    r.current.host = h;
    AsyncStorage.setItem(HOST_KEY, h).catch(() => {});
  };

  const flush = useCallback(async () => {
    const s = r.current;
    if (s.inFlight) return;
    const url = ingestUrl(s.host);
    if (!url) {
      setNet((n) => ({ ...n, queued: s.outbox.length, last: 'invalid laptop address' }));
      return;
    }
    s.inFlight = true;
    try {
      while (s.outbox.length) {
        const body = s.outbox[0];
        const ctl = new AbortController();
        const to = setTimeout(() => ctl.abort(), POST_TIMEOUT_MS);
        try {
          const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
            signal: ctl.signal,
          });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          s.outbox.shift();
          // optional tracker estimate in the reply (CONTRACT.md, "position"); absent today unless the laptop adds it
          const reply = await res.json().catch(() => null);
          const est = parseServerPosition(reply, s.section);
          if (est) setServerPos(est);
          setNet((n) => ({ ...n, sent: n.sent + 1, queued: s.outbox.length, last: 'ok' }));
        } catch (e) {
          const msg = e instanceof Error ? (e.name === 'AbortError' ? 'timeout' : e.message) : 'error';
          setNet((n) => ({ ...n, failed: n.failed + 1, queued: s.outbox.length, last: msg }));
          break; // retry on the next tick
        } finally {
          clearTimeout(to);
        }
      }
    } finally {
      s.inFlight = false;
    }
  }, []);

  const tick = useCallback(() => {
    const s = r.current;
    s.t += 1;
    const motion = s.motion;
    const mag = s.mag;
    const baroN = s.baroN;
    s.motion = [];
    s.mag = [];
    s.baroN = 0;

    for (const k of s.knocks.close(s.lastSampleT)) if (k.n === 2) s.knockFlag = true;
    const knock = s.knockFlag;
    s.knockFlag = false;

    const m = summariseMotion(motion, s.hState);
    const state = s.stops.step(m ? m.vib_rms : null, m ? m.yaw_rate_dps : null, m ? m.handled : false);
    const nowS = Date.now() / 1000;
    s.baro = s.baro.filter((b) => nowS - b.t <= 30);
    const lastBaro = s.baro.length ? s.baro[s.baro.length - 1] : null;

    const out: Row = {
      source: 'tubesense-mobile',
      session: s.session,
      t: s.t,
      ts: new Date().toISOString(),
      yaw_rate_dps: round(m ? m.yaw_rate_dps : 0, 3) ?? 0,
      acc_h_rms: round(m ? m.acc_h_rms : 0, 4) ?? 0,
      vib_rms: round(m ? m.vib_rms : 0, 4) ?? 0,
      b_abs_ut: round(magnitudeMean(mag), 2),
      pressure_hpa: lastBaro && nowS - lastBaro.t <= 10 ? round(lastBaro.p, 3) : null,
      d_pressure_10s: round(pressureChange(s.baro, nowS, 10), 3),
      state,
      knock,
      rates: { motion: motion.length, mag: mag.length, baro: baroN },
    };
    s.outbox = enqueue(s.outbox, out, OUTBOX_MAX);
    s.guess.step(state);
    setGuessPos(s.guess.position());
    setRow(out);
    setYawHist((h) => [...h, out.yaw_rate_dps].slice(-SPARK_S));
    if (knock) setKnockAt(Date.now());
    setNow(Date.now());
    flush();
  }, [flush]);

  const startCapture = async () => {
    setError(null);
    const s = r.current;
    try {
      const perm = await DeviceMotion.requestPermissionsAsync();
      if (!perm.granted) {
        setError('Motion & Fitness permission denied. Allow it for Expo Go in iOS Settings > Privacy & Security > Motion & Fitness.');
        return;
      }
      if (!(await DeviceMotion.isAvailableAsync())) {
        setError('DeviceMotion is not available on this device.');
        return;
      }
      const [magOk, baroOk] = await Promise.all([
        Magnetometer.isAvailableAsync().catch(() => false),
        Barometer.isAvailableAsync().catch(() => false),
      ]);

      s.session = sessionId();
      s.t = 0;
      s.motion = [];
      s.mag = [];
      s.baro = [];
      s.baroN = 0;
      s.knocks = new KnockDetector();
      s.knockFlag = false;
      s.stops = new StopDetector();
      s.hState = { h: [0, 0, 0], t: null };
      s.outbox = [];
      s.guess = new TimeGuess(s.section);
      setGuessPos(null);
      setServerPos(null);
      setNet({ sent: 0, failed: 0, queued: 0, last: '' });
      setYawHist([]);
      setRow(null);

      DeviceMotion.setUpdateInterval(MOTION_MS);
      s.subs.push(
        DeviceMotion.addListener((d) => {
          const a = d.acceleration;
          const ag = d.accelerationIncludingGravity;
          const rr = d.rotationRate;
          if (!a || !ag || !rr) return;
          const t = typeof a.timestamp === 'number' && a.timestamp > 0 ? a.timestamp : Date.now() / 1000;
          const acc: Vec3 = [a.x, a.y, a.z];
          s.motion.push({
            t,
            acc,
            down: [ag.x - a.x, ag.y - a.y, ag.z - a.z],
            omega: rotationVector(rr, Platform.OS),
          });
          s.lastSampleT = t;
          for (const k of s.knocks.push(t, Math.hypot(a.x, a.y, a.z))) if (k.n === 2) s.knockFlag = true;
        }),
      );
      if (magOk) {
        Magnetometer.setUpdateInterval(MAG_MS);
        s.subs.push(Magnetometer.addListener((b) => s.mag.push([b.x, b.y, b.z])));
      }
      if (baroOk) {
        Barometer.setUpdateInterval(BARO_MS);
        s.subs.push(
          Barometer.addListener((b) => {
            s.baroN += 1;
            s.baro.push({ t: Date.now() / 1000, p: b.pressure });
          }),
        );
      }
      await activateKeepAwakeAsync('tubesense-capture').catch(() => {});
      s.timer = setInterval(tick, 1000);
      setRunning(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      stopCapture();
    }
  };

  function stopCapture() {
    const s = r.current;
    if (s.timer) clearInterval(s.timer);
    s.timer = null;
    s.subs.forEach((x) => x.remove());
    s.subs = [];
    s.motion = [];
    s.mag = [];
    deactivateKeepAwake('tubesense-capture').catch(() => {});
    setRunning(false);
  }

  const knockOn = Date.now() - knockAt < 1500;
  const stopped = row?.state === 'stopped';
  const online = net.last === 'ok';
  const P = tunnel ? TUNNEL : LIGHT;
  const st = useMemo(() => makeStyles(P), [P]);
  const { width, height } = useWindowDimensions();
  const pos = serverPos ?? (running ? guessPos : null);
  const remaining = pos ? Math.max(0, pos.lengthM - pos.sM) : section.lengthM;
  const from = STATIONS[section.from], to = STATIONS[section.to];

  const pickSection = (sec: Section) => {
    if (running) return;
    setSection(sec);
    r.current.section = sec;
    AsyncStorage.setItem(SECTION_KEY, sec.key).catch(() => {});
  };
  const toggleTunnel = () => {
    setTunnel((v) => {
      AsyncStorage.setItem(TUNNEL_KEY, v ? '0' : '1').catch(() => {});
      return !v;
    });
  };
  const linkLine = !running
    ? `Laptop ${host}:8770`
    : online
      ? `Sending · ${net.sent} sent`
      : net.last
        ? `No signal to the laptop · ${net.queued}/${OUTBOX_MAX} queued`
        : 'Connecting to the laptop…';

  return (
    <View style={st.root}>
      <StatusBar style={tunnel ? 'light' : 'dark'} />
      <RideMap
        width={width}
        height={height}
        section={section}
        pos={pos}
        theme={tunnel ? TUNNEL_MAP : LIGHT_MAP}
        topInset={running ? 200 : 170}
        bottomInset={running ? 300 : 330}
      />

      {/* ---- top: route card before the ride, next-station card during it */}
      {!running ? (
        <View style={st.routeCard}>
          <View style={st.routeRow}>
            <View style={[st.routeDot, { backgroundColor: P.accent }]} />
            <Text style={st.routeText}>{from.name}</Text>
            <Pressable onPress={() => pickSection(reverseSection(section))} accessibilityRole="button" accessibilityLabel="Swap direction" style={st.iconBtn}>
              <Icon name="swap" color={P.ink} />
            </Pressable>
          </View>
          <View style={st.routeDivider} />
          <View style={st.routeRow}>
            <View style={[st.routeDot, st.routeDotHollow, { borderColor: P.accent }]} />
            <Pressable onPress={() => pickSection(nextSection(section) === section ? reverseSection(section) : nextSection(section))} accessibilityRole="button" accessibilityLabel={`Destination ${to.name}. Tap for the next station`} style={{ flex: 1 }}>
              <Text style={st.routeText}>{to.name}</Text>
            </Pressable>
            <Pressable onPress={toggleTunnel} accessibilityRole="button" accessibilityLabel={tunnel ? 'Day map' : 'Tunnel map'} style={st.iconBtn}>
              <Icon name={tunnel ? 'sun' : 'moon'} color={P.ink} />
            </Pressable>
          </View>
        </View>
      ) : (
        <>
          <View style={st.nextCard} accessibilityRole="summary">
            <Text style={st.nextBig}>{fmtM(remaining)}</Text>
            <Text style={st.nextTo}>to {to.name}</Text>
          </View>
          <View style={[st.stateBadge, { backgroundColor: !row ? P.surface2 : stopped ? P.amber : P.green }]}>
            <Text style={[st.stateText, { color: !row ? P.ink3 : P.onAmber }]}>{!row ? '…' : stopped ? 'Stopped' : 'Moving'}</Text>
          </View>
          <Pressable onPress={toggleTunnel} accessibilityRole="button" accessibilityLabel={tunnel ? 'Day map' : 'Tunnel map'} style={[st.iconBtn, st.floatBtn]}>
            <Icon name={tunnel ? 'sun' : 'moon'} color={P.ink} />
          </Pressable>
          {knockOn && (
            <View style={st.knockBanner} accessibilityLiveRegion="polite">
              <Text style={st.knockTitle}>Sync knock detected</Text>
              <Text style={st.knockSub}>Ride start is lined up with the laptop</Text>
            </View>
          )}
        </>
      )}

      {/* ---- bottom sheet */}
      <View style={st.sheet}>
        <View style={st.grabber} />
        {!running ? (
          <>
            <View style={st.sheetHead}>
              <View style={{ flex: 1 }}>
                <Text style={st.kicker}>Central line · {section.key.startsWith('EB') ? 'eastbound' : 'westbound'}</Text>
                <Text style={st.title}>{(section.lengthM / 1000).toFixed(2)} km · 1 stop</Text>
              </View>
              <Pressable onPress={() => setDetails(true)} accessibilityRole="button" style={[st.chip, { backgroundColor: P.surface2 }]}>
                <View style={[st.dot, { backgroundColor: net.last === 'ok' ? P.green : P.ink3 }]} />
                <Text style={st.chipText}>Laptop</Text>
              </Pressable>
            </View>
            <View style={st.tips}>
              <Text style={st.tip}>Knock the case twice after you start</Text>
              <Text style={st.tip}>No microphone is used</Text>
            </View>
            {error && <Text style={st.error}>{error}</Text>}
            <Pressable onPress={startCapture} style={({ pressed }) => [st.primary, pressed && { opacity: 0.85 }]} accessibilityRole="button">
              <Text style={st.primaryText}>Start ride</Text>
            </Pressable>
          </>
        ) : (
          <>
            <View style={st.stats}>
              <Stat st={st} value={fmtClock(row?.t ?? 0)} label="riding" />
              <Stat st={st} value={pos ? fmtM(pos.sM) : '–'} label={`from ${from.short}`} />
              <Stat st={st} value={pos?.source === 'tracker' ? 'tracker' : pos ? 'guess' : '–'} label="position" />
            </View>
            <View>
              <View style={st.progress}>
                <View style={[st.progressDone, { width: `${pos ? Math.min(100, (pos.sM / pos.lengthM) * 100) : 0}%` }]} />
              </View>
              <View style={st.ends}>
                <Text style={st.endText}>{from.short}</Text>
                <Text style={st.endText}>{to.short}</Text>
              </View>
              <Text style={st.muted}>
                {pos?.source === 'tracker'
                  ? 'Position from the laptop tracker (no GPS).'
                  : `Rough guess: moving time ÷ ${section.usualRunS} s (${section.usualNote}). Not GPS.`}
              </Text>
            </View>
            <View style={st.actions}>
              <Pressable onPress={() => setDetails(true)} style={[st.link, { flex: 1 }]} accessibilityRole="button">
                <View style={[st.dot, { backgroundColor: online ? P.green : net.last ? P.red : P.ink3 }]} />
                <Text style={st.linkText} numberOfLines={1}>{linkLine}</Text>
              </Pressable>
              <Pressable onPress={stopCapture} style={st.stop} accessibilityRole="button">
                <Text style={st.stopText}>End ride</Text>
              </Pressable>
            </View>
          </>
        )}
      </View>

      {/* ---- details: the sensor values and the laptop address (the old one-page screen, now on demand) */}
      {details && (
        <View style={st.overlay}>
          <Pressable style={StyleSheet.absoluteFill} onPress={() => setDetails(false)} accessibilityLabel="Close details" />
          <View style={st.details}>
            <View style={st.grabber} />
            <View style={st.sheetHead}>
              <Text style={[st.title, { flex: 1 }]}>Sensor details</Text>
              <Pressable onPress={() => setDetails(false)} style={st.iconBtn} accessibilityRole="button" accessibilityLabel="Close">
                <Icon name="close" color={P.ink} />
              </Pressable>
            </View>
            <ScrollView contentContainerStyle={{ gap: 12, paddingBottom: 24 }} keyboardShouldPersistTaps="handled">
              <View style={st.grid}>
                <Stat st={st} box value={row?.pressure_hpa != null ? row.pressure_hpa.toFixed(2) : '–'} label="pressure, hPa" />
                <Stat st={st} box value={row?.d_pressure_10s != null ? (row.d_pressure_10s >= 0 ? '+' : '') + row.d_pressure_10s.toFixed(2) : '–'} label="change in 10 s, hPa" />
                <Stat st={st} box value={row?.b_abs_ut != null ? row.b_abs_ut.toFixed(1) : '–'} label="|B|, µT (calibrated)" />
                <Stat st={st} box value={row ? row.vib_rms.toFixed(3) : '–'} label="vibration, m/s² rms" />
                <Stat st={st} box value={row ? row.acc_h_rms.toFixed(3) : '–'} label="horizontal accel., m/s² rms" />
                <Stat st={st} box value={row ? `${row.rates.motion}/${row.rates.mag}/${row.rates.baro}` : '–'} label="motion/mag/baro Hz" />
              </View>
              <View style={st.box}>
                <Text style={st.label}>Turning · last 60 s</Text>
                <Text style={st.value}>{row ? `${row.yaw_rate_dps.toFixed(2)} °/s` : '–'}</Text>
                <View onLayout={(e: LayoutChangeEvent) => setSparkW(e.nativeEvent.layout.width)}>
                  <Spark data={yawHist} width={sparkW} height={80} line={P.line} stroke={P.accent} />
                </View>
                <Text style={st.muted}>+ = left turn (rotation about gravity)</Text>
              </View>
              <View style={st.box}>
                <Text style={st.label}>Laptop address</Text>
                <View style={st.hostRow}>
                  <TextInput
                    style={st.input}
                    value={host}
                    onChangeText={onHost}
                    placeholder={DEFAULT_HOST}
                    placeholderTextColor={P.ink3}
                    autoCapitalize="none"
                    autoCorrect={false}
                    keyboardType="numbers-and-punctuation"
                    accessibilityLabel="Laptop address"
                  />
                  <Text style={st.port}>:8770</Text>
                </View>
                <Text style={st.muted} numberOfLines={1}>{ingestUrl(host) ?? 'Enter an IP such as 172.20.10.3'}</Text>
                <Text style={st.muted}>sent {net.sent} · failed {net.failed} · queued {net.queued}/{OUTBOX_MAX}{net.last ? ` · last: ${net.last}` : ''}</Text>
                <Text style={st.muted}>Session {row?.session ?? 'starts at Start'} · random per Start, not tied to you or the phone</Text>
              </View>
              <Text style={st.foot}>Indicative values, not a certified instrument. No microphone. Raw samples never leave the phone; 1-second summaries only.</Text>
            </ScrollView>
          </View>
        </View>
      )}
    </View>
  );
}

function Stat({ st, value, label, box }: { st: Styles; value: string; label: string; box?: boolean }) {
  return (
    <View style={box ? st.statBox : st.stat}>
      <Text style={box ? st.statBoxValue : st.statValue}>{value}</Text>
      <Text style={st.statLabel}>{label}</Text>
    </View>
  );
}

function Icon({ name, color }: { name: 'swap' | 'sun' | 'moon' | 'close'; color: string }) {
  const p = { stroke: color, strokeWidth: 2, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, fill: 'none' };
  return (
    <Svg width={20} height={20} viewBox="0 0 24 24">
      {name === 'swap' && <Path d="M7 4v16M7 4L3 8M7 4l4 4M17 20V4M17 20l-4-4M17 20l4-4" {...p} />}
      {name === 'sun' && (
        <>
          <Circle cx={12} cy={12} r={4} {...p} />
          <Path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" {...p} />
        </>
      )}
      {name === 'moon' && <Path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" {...p} />}
      {name === 'close' && <Path d="M6 6l12 12M18 6L6 18" {...p} />}
    </Svg>
  );
}

function Spark({ data, width, height, line, stroke }: { data: number[]; width: number; height: number; line: string; stroke: string }) {
  const lim = Math.max(2, ...data.map((v) => Math.abs(v)));
  const x = (i: number) => (i / (SPARK_S - 1)) * width;
  const y = (v: number) => height / 2 - (v / lim) * (height / 2 - 4);
  const off = SPARK_S - data.length;
  const pts = data.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return (
    <Svg width={width} height={height}>
      <Line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke={line} strokeWidth={1} />
      {data.length > 1 && <Polyline points={pts} fill="none" stroke={stroke} strokeWidth={2.5} />}
    </Svg>
  );
}

type Styles = ReturnType<typeof makeStyles>;
function makeStyles(P: Palette) {
  const float = { shadowColor: '#28326E', shadowOpacity: 0.28, shadowRadius: 20, shadowOffset: { width: 0, height: 10 }, elevation: 8 };
  return StyleSheet.create({
    root: { flex: 1, backgroundColor: P.bg },
    routeCard: { position: 'absolute', left: 16, right: 16, top: 58, backgroundColor: P.sheet, borderRadius: 24, paddingHorizontal: 18, paddingVertical: 12, ...float },
    routeRow: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 44 },
    routeDot: { width: 12, height: 12, borderRadius: 6 },
    routeDotHollow: { backgroundColor: 'transparent', borderWidth: 3 },
    routeText: { color: P.ink, fontSize: 16, fontWeight: '700', flex: 1 },
    routeDivider: { height: 1, backgroundColor: P.line, marginLeft: 24 },
    iconBtn: { width: 44, height: 44, borderRadius: 14, backgroundColor: P.surface2, alignItems: 'center', justifyContent: 'center' },
    floatBtn: { position: 'absolute', right: 16, top: 170, backgroundColor: P.sheet, ...float },
    nextCard: { position: 'absolute', left: 16, top: 58, width: 230, backgroundColor: P.card, borderRadius: 24, paddingHorizontal: 18, paddingVertical: 14, ...float },
    nextBig: { color: P.cardInk, fontSize: 32, fontWeight: '800', letterSpacing: -1, fontVariant: ['tabular-nums'] },
    nextTo: { color: P.cardSub, fontSize: 16, fontWeight: '700' },
    stateBadge: { position: 'absolute', right: 16, top: 58, width: 96, height: 96, borderRadius: 48, alignItems: 'center', justifyContent: 'center', borderWidth: 5, borderColor: P.sheet, ...float },
    stateText: { fontSize: 16, fontWeight: '800' },
    knockBanner: { position: 'absolute', left: 16, right: 16, top: 166, marginRight: 64, backgroundColor: '#FFC53D', borderRadius: 20, paddingHorizontal: 16, paddingVertical: 12, ...float },
    knockTitle: { color: '#1F1203', fontSize: 16, fontWeight: '800' },
    knockSub: { color: '#1F1203', fontSize: 12, fontWeight: '600' },
    sheet: { position: 'absolute', left: 0, right: 0, bottom: 0, backgroundColor: P.sheet, borderTopLeftRadius: 30, borderTopRightRadius: 30, paddingHorizontal: 20, paddingTop: 10, paddingBottom: 34, gap: 14, ...float },
    grabber: { alignSelf: 'center', width: 40, height: 5, borderRadius: 3, backgroundColor: P.line },
    sheetHead: { flexDirection: 'row', alignItems: 'center', gap: 12 },
    kicker: { color: P.ink3, fontSize: 13, fontWeight: '600' },
    title: { color: P.ink, fontSize: 26, fontWeight: '800', letterSpacing: -0.6 },
    chip: { flexDirection: 'row', alignItems: 'center', gap: 6, height: 34, paddingHorizontal: 12, borderRadius: 17 },
    chipText: { color: P.ink2, fontSize: 13, fontWeight: '700' },
    tips: { flexDirection: 'row', gap: 10 },
    tip: { flex: 1, color: P.ink2, fontSize: 13, lineHeight: 18, backgroundColor: P.surface2, borderRadius: 16, padding: 12, overflow: 'hidden' },
    primary: { height: 60, borderRadius: 20, backgroundColor: P.accent, alignItems: 'center', justifyContent: 'center' },
    primaryText: { color: P.onAccent, fontSize: 18, fontWeight: '800' },
    error: { color: P.red, fontSize: 14 },
    stats: { flexDirection: 'row' },
    stat: { flex: 1, alignItems: 'center' },
    statValue: { color: P.ink, fontSize: 22, fontWeight: '800', letterSpacing: -0.5, fontVariant: ['tabular-nums'] },
    statLabel: { color: P.ink3, fontSize: 12 },
    progress: { height: 10, borderRadius: 5, backgroundColor: P.surface2, overflow: 'hidden' },
    progressDone: { height: 10, borderRadius: 5, backgroundColor: P.accent },
    ends: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 6 },
    endText: { color: P.ink3, fontSize: 12 },
    muted: { color: P.ink3, fontSize: 12, lineHeight: 17, marginTop: 4 },
    actions: { flexDirection: 'row', gap: 10, alignItems: 'center' },
    link: { flexDirection: 'row', alignItems: 'center', gap: 8, height: 48, paddingHorizontal: 14, borderRadius: 16, backgroundColor: P.surface2 },
    linkText: { color: P.ink2, fontSize: 13, flexShrink: 1 },
    stop: { height: 48, paddingHorizontal: 22, borderRadius: 16, backgroundColor: P.redSoft, alignItems: 'center', justifyContent: 'center' },
    stopText: { color: P.red, fontSize: 15, fontWeight: '800' },
    dot: { width: 9, height: 9, borderRadius: 5 },
    overlay: { position: 'absolute', left: 0, right: 0, top: 0, bottom: 0, backgroundColor: 'rgba(3,5,9,0.55)', justifyContent: 'flex-end' },
    details: { maxHeight: '82%', backgroundColor: P.sheet, borderTopLeftRadius: 30, borderTopRightRadius: 30, paddingHorizontal: 20, paddingTop: 10, gap: 12 },
    grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
    statBox: { flexGrow: 1, flexBasis: '45%', backgroundColor: P.surface2, borderRadius: 16, padding: 12, gap: 2 },
    statBoxValue: { color: P.ink, fontSize: 20, fontWeight: '800', fontVariant: ['tabular-nums'] },
    box: { backgroundColor: P.surface2, borderRadius: 16, padding: 14, gap: 6 },
    label: { color: P.ink3, fontSize: 12, fontWeight: '700' },
    value: { color: P.ink, fontSize: 16, fontWeight: '700', fontVariant: ['tabular-nums'] },
    hostRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
    input: { flex: 1, color: P.ink, fontSize: 18, backgroundColor: P.sheet, borderColor: P.line, borderWidth: 1, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10, fontVariant: ['tabular-nums'] },
    port: { color: P.ink2, fontSize: 16 },
    foot: { color: P.ink3, fontSize: 12, textAlign: 'center' },
  });
}
