import AsyncStorage from '@react-native-async-storage/async-storage';
import { activateKeepAwakeAsync, deactivateKeepAwake } from 'expo-keep-awake';
import { Barometer, DeviceMotion, Magnetometer } from 'expo-sensors';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
  type LayoutChangeEvent,
} from 'react-native';
import Svg, { Line, Polyline } from 'react-native-svg';

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

// Tokens from webapp/static/css/app.css
const C = {
  bg: '#070B14',
  surface: '#0D1424',
  surface2: '#121C31',
  line: '#23314F',
  navy: '#0E2E68',
  blue: '#1F4C95',
  ink: '#E9F1FF',
  ink2: '#A9C2EC',
  ink3: '#8398C0',
  amber: '#F2B33D',
  onAmber: '#1A1203',
  green: '#4ADE80',
  red: '#F87171',
  cyan: '#4FC3F7',
};

const HOST_KEY = 'tubesense.laptopHost';
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

  return (
    <View style={st.root}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={st.scroll} keyboardShouldPersistTaps="handled">
        <Text style={st.kicker}>TUBESENSE</Text>
        <Text style={st.h1}>Ride capture</Text>
        <Text style={st.privacy}>No microphone. Raw samples never leave the phone; 1-second summaries only.</Text>

        <View style={st.card}>
          <Text style={st.label}>LAPTOP ADDRESS</Text>
          <View style={st.hostRow}>
            <TextInput
              style={st.input}
              value={host}
              onChangeText={onHost}
              placeholder={DEFAULT_HOST}
              placeholderTextColor={C.ink3}
              autoCapitalize="none"
              autoCorrect={false}
              keyboardType="numbers-and-punctuation"
            />
            <Text style={st.port}>:8770</Text>
          </View>
          <Text style={st.muted} numberOfLines={1}>
            {ingestUrl(host) ?? 'Enter an IP such as 172.20.10.3'}
          </Text>
        </View>

        <Pressable
          onPress={running ? stopCapture : startCapture}
          style={({ pressed }) => [st.btn, running && st.btnStop, pressed && { opacity: 0.8 }]}
          accessibilityRole="button"
        >
          <Text style={[st.btnText, running && { color: C.ink }]}>{running ? 'Stop' : 'Start'}</Text>
        </Pressable>
        {error && <Text style={st.error}>{error}</Text>}

        <View style={[st.badge, stopped ? st.badgeStopped : st.badgeMoving, !row && st.badgeIdle]}>
          <Text style={[st.badgeText, !row && { color: C.ink3 }]}>
            {!row ? 'IDLE' : stopped ? 'STOPPED' : 'MOVING'}
          </Text>
          <Text style={st.badgeSub}>
            {row ? `t ${row.t}s · session ${row.session}` : 'Press Start, then knock the case twice'}
          </Text>
        </View>

        <View style={[st.knock, knockOn && st.knockOn]}>
          <Text style={[st.knockText, knockOn && { color: C.onAmber }]}>
            {knockOn ? 'SYNC KNOCK' : 'sync knock: knock the case twice'}
          </Text>
        </View>

        <View style={st.card}>
          <View style={st.cardHead}>
            <Text style={st.label}>YAW RATE · LAST 60 S</Text>
            <Text style={st.value}>{row ? `${row.yaw_rate_dps.toFixed(2)} °/s` : '—'}</Text>
          </View>
          <View onLayout={(e: LayoutChangeEvent) => setSparkW(e.nativeEvent.layout.width)}>
            <Spark data={yawHist} width={sparkW} height={90} />
          </View>
          <Text style={st.muted}>+ = left turn (rotation about gravity)</Text>
        </View>

        <View style={st.grid}>
          <Stat label="PRESSURE" value={row?.pressure_hpa != null ? row.pressure_hpa.toFixed(2) : '—'} unit="hPa" />
          <Stat
            label="Δ PRESSURE 10 S"
            value={row?.d_pressure_10s != null ? (row.d_pressure_10s >= 0 ? '+' : '') + row.d_pressure_10s.toFixed(2) : '—'}
            unit="hPa"
          />
          <Stat label="|B|" value={row?.b_abs_ut != null ? row.b_abs_ut.toFixed(1) : '—'} unit="µT" />
          <Stat label="VIBRATION" value={row ? row.vib_rms.toFixed(3) : '—'} unit="m/s² rms" />
          <Stat label="HORIZ. ACCEL" value={row ? row.acc_h_rms.toFixed(3) : '—'} unit="m/s² rms" />
          <Stat
            label="SENSOR RATES"
            value={row ? `${row.rates.motion}/${row.rates.mag}/${row.rates.baro}` : '—'}
            unit="motion/mag/baro Hz"
          />
        </View>

        <View style={st.card}>
          <View style={st.cardHead}>
            <Text style={st.label}>CONNECTION</Text>
            <View style={[st.dot, { backgroundColor: !running ? C.ink3 : online ? C.green : C.red }]} />
          </View>
          <Text style={st.netLine}>
            sent <Text style={st.value}>{net.sent}</Text>   failed <Text style={st.value}>{net.failed}</Text>   queued{' '}
            <Text style={st.value}>{net.queued}</Text>/{OUTBOX_MAX}
          </Text>
          <Text style={st.muted}>{net.last ? `last: ${net.last}` : 'POST every second to /api/live/ingest'}</Text>
        </View>

        <Text style={st.foot}>
          Optional companion to phyphox. Indicative values, not a certified instrument. Random session ID per Start.
        </Text>
      </ScrollView>
    </View>
  );
}

function Stat({ label, value, unit }: { label: string; value: string; unit: string }) {
  return (
    <View style={st.stat}>
      <Text style={st.label}>{label}</Text>
      <Text style={st.statValue}>{value}</Text>
      <Text style={st.muted}>{unit}</Text>
    </View>
  );
}

function Spark({ data, width, height }: { data: number[]; width: number; height: number }) {
  const lim = Math.max(2, ...data.map((v) => Math.abs(v)));
  const x = (i: number) => (i / (SPARK_S - 1)) * width;
  const y = (v: number) => height / 2 - (v / lim) * (height / 2 - 4);
  const off = SPARK_S - data.length;
  const pts = data.map((v, i) => `${x(i + off).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  return (
    <Svg width={width} height={height}>
      <Line x1={0} y1={height / 2} x2={width} y2={height / 2} stroke={C.line} strokeWidth={1} />
      {data.length > 1 && <Polyline points={pts} fill="none" stroke={C.amber} strokeWidth={2} />}
    </Svg>
  );
}

const st = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  scroll: { paddingHorizontal: 16, paddingTop: 64, paddingBottom: 48, gap: 12 },
  kicker: { color: C.amber, fontSize: 13, fontWeight: '700', letterSpacing: 2 },
  h1: { color: C.ink, fontSize: 30, fontWeight: '700' },
  privacy: { color: C.ink2, fontSize: 14, lineHeight: 20 },
  card: { backgroundColor: C.surface2, borderColor: C.line, borderWidth: 1, borderRadius: 12, padding: 14, gap: 6 },
  cardHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  label: { color: C.ink3, fontSize: 12, fontWeight: '600', letterSpacing: 1 },
  value: { color: C.ink, fontSize: 16, fontWeight: '600', fontVariant: ['tabular-nums'] },
  muted: { color: C.ink2, fontSize: 12 },
  hostRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  input: {
    flex: 1,
    color: C.ink,
    fontSize: 18,
    backgroundColor: C.surface,
    borderColor: C.blue,
    borderWidth: 1,
    borderRadius: 8,
    paddingHorizontal: 10,
    paddingVertical: 8,
    fontVariant: ['tabular-nums'],
  },
  port: { color: C.ink2, fontSize: 16 },
  btn: { backgroundColor: C.amber, borderRadius: 12, paddingVertical: 16, alignItems: 'center' },
  btnStop: { backgroundColor: C.navy, borderWidth: 1, borderColor: C.blue },
  btnText: { color: C.onAmber, fontSize: 20, fontWeight: '700', letterSpacing: 1 },
  error: { color: C.red, fontSize: 14 },
  badge: { borderRadius: 16, paddingVertical: 22, alignItems: 'center', borderWidth: 2, gap: 4 },
  badgeMoving: { backgroundColor: C.navy, borderColor: C.amber },
  badgeStopped: { backgroundColor: '#0F2A1C', borderColor: C.green },
  badgeIdle: { backgroundColor: C.surface, borderColor: C.line },
  badgeText: { color: C.ink, fontSize: 44, fontWeight: '800', letterSpacing: 3 },
  badgeSub: { color: C.ink2, fontSize: 12 },
  knock: { borderRadius: 10, borderWidth: 1, borderColor: C.line, paddingVertical: 10, alignItems: 'center' },
  knockOn: { backgroundColor: C.amber, borderColor: C.amber },
  knockText: { color: C.ink3, fontSize: 14, fontWeight: '700', letterSpacing: 1 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
  stat: {
    flexGrow: 1,
    flexBasis: '45%',
    backgroundColor: C.surface2,
    borderColor: C.line,
    borderWidth: 1,
    borderRadius: 12,
    padding: 12,
    gap: 2,
  },
  statValue: { color: C.ink, fontSize: 22, fontWeight: '700', fontVariant: ['tabular-nums'] },
  dot: { width: 10, height: 10, borderRadius: 5 },
  netLine: { color: C.ink2, fontSize: 14 },
  foot: { color: C.ink3, fontSize: 12, textAlign: 'center', marginTop: 8 },
});
