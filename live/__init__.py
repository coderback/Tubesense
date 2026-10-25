"""Live sensing from a phone running phyphox ("Allow remote access"), read over the local network.

phyphox.py       client for the phyphox remote-interface REST API (/config, /get, /control, /meta, /time)
mock_phyphox.py  a local phyphox-compatible server replaying a recorded ride (or synthetic motion): labelled MOCK
detect.py        causal per-second detectors reusing the frozen tubesense/ logic (yaw about gravity, stop
                 detection, knock/double-knock, placement change, pressure step, |B|)
capture.py       CLI + run_capture() API: polls ~5 Hz, emits JSONL events, optionally inserts the per-second rows
                 into Atlas tubesense.ingest_live (the Stream Processing source)

What this is NOT: it does not position the train. Live capture shows what the phone senses now (turning, stopped /
moving, knocks, placement, pressure); positioning needs the per-section maps and runs offline / on replays.
Privacy: no audio is requested (phyphox audio buffers are never polled); only derived per-second values leave the
laptop; raw samples stay in memory unless --save-raw writes them under the gitignored data/raw/live/.
"""
