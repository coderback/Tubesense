# Tubesense: 3-minute demo script

Open http://127.0.0.1:8770/#/demo (press Ctrl+Shift+R once if a screen fails to load).
→ or PageDown = next step, Space = play/pause, Esc = leave demo mode.
About 420 words of narration in total, which is roughly 2:50 at a normal pace.

---

## 0:00–0:20 · Step 1, the problem (Overview)
**Show:** the 3D map and the 87 dB card.
**Say:**
> "This is the Central line between Liverpool Street and Bethnal Green. TfL's own in-cab measurement here is 87 decibels. The press reports 92.8, but we couldn't find that figure's source, so we don't use it. TfL says the cause comes back after grinding, but it doesn't publish section-level data."

## 0:20–0:35 · Step 2, the named user
**Show:** the Elly Baker AM card.
**Say:**
> "In July, Assembly Member Elly Baker asked TfL for this data, and most of her requests were turned down. Tubesense is built for her office, and for everyone who lives with that noise: drivers in the cab every shift, passengers, and residents above the line."

## 0:35–1:15 · Step 3, live tracking (Ride) → press Next, let it play
**Show:** the dot moving on the 3D map, the 90 % band, the distance to Bethnal Green, noise now against usual.
**Say:**
> "This is a ride I recorded yesterday, on an ordinary phone, but the tracker is running now. There's no GPS in a tunnel. Every curve twists the phone the same way on every ride, so the gyroscope matches it against earlier rides. In a test we wrote down before the rides, the median error was about 2 metres, against 8 for a timetable guess. The noise is relative phone audio, not a certified meter, and we say so on screen."

*Optional, 10 s:* switch to the **Live phone** tab, turn the phone and point at the trace: "and it reads my phone live, right now."

## 1:15–1:50 · Step 4, the agent loop
**Show:** the recorded Argon analyst session, the hash chain check, and the roughness-chart card.
**Say:**
> "Gemini 4 Argon tuned the tracker, but it can't mark its own homework. A scorer it can't edit decides, every attempt goes into a tamper-evident log, and it got one sealed test. It matched the baseline and didn't beat it, and we report that. Argon also read TfL's own rail-roughness charts from a PDF, and our checks against the pixels caught its mistakes."

## 1:50–2:30 · Step 5, verified answer
**Show:** the question, Atlas searching three ways, Argon's claims with verifier verdicts, the seeded false claim REJECTED.
**Say:**
> "Ask Argon a question. MongoDB Atlas retrieves TfL's records with full-text, vector and hybrid search. Argon answers in cited claims, and code checks every quote word for word and every number back to its source. Here's a planted false claim, '92.8 decibels is TfL's own data', and the verifier rejects it."

*(If the live answer is still running, the cached answer is labelled CACHED and re-checked now. Say "this is the saved answer, re-verified just now".)*

## 2:30–3:00 · Step 6, approve
**Do:** tick the five boxes, type your name, click **Approve as draft**.
**Say:**
> "Nothing goes out without a human. I tick the checklist and sign, the claims are re-verified at that moment, and it creates a signed draft. Nothing is sent. Ordinary rides. No GPS. Every claim checked. A human signs."

---

## If something goes wrong
- **Ride doesn't start:** press "Play PRECOMPUTED instead". It's labelled.
- **Ask Argon slow:** keep talking; the cached answer is already on screen.
- **Screen fails to load:** Ctrl+Shift+R.
- **Over time:** cut the optional live-phone moment first, then shorten step 4.

## Say these, not stronger versions
- "about 2 metres on new rides". Not "2-metre accuracy everywhere"; between curves it's no better than a timetable.
- "relative phone levels". Not "measured decibels".
- "92.8 is a press figure". Never "TfL's 92.8".
- "TfL attributes the noise to corrugation". We didn't measure corrugation.
