# Tubesense: privacy

Tubesense records Tube rides with a phone's motion sensors and microphone. A microphone on public
transport can pick up other people's speech, so the design starts from one rule: **keep no audio at
all**. This page lists what data exists, where it goes, and how long it is kept.

Current guidance we design against: the ICO's guidance on video surveillance (including CCTV) says
organisations "should not normally" record conversations between members of the public, that doing so
is "highly intrusive", and that audio recording should be off by default. The guidance is under review
following the Data (Use and Access) Act 2025.

## Who the data is about

The hackathon dataset is **one consenting contributor's own journeys** (Tobi, 23 and 24 Oct 2026).
No one else contributed data. Other passengers may be audible in the raw recordings; that is the risk
the design below addresses.

## Data inventory

| Data | Contents | Where it lives | Leaves the laptop? | Retention |
|---|---|---|---|---|
| Raw audio (`data/raw/audio/*.m4a`) | Voice Memos recordings of each ride | Laptop only; gitignored (`*.m4a`, `*.wav`, ... anywhere in the tree) | No | **Deleted on 1 Nov 2026**, a week after the pre-registered analysis, from the laptop and the phone. Voice Memos iCloud sync is on the pre-ride checklist to be switched off; any copies already in iCloud are deleted on the same date. |
| Raw sensors (`data/raw/phyphox/*.xlsx`) | Accelerometer, gyroscope, magnetometer, barometer; device model, OS version, wall-clock start and pause times | Laptop only; gitignored | No | Kept for re-analysis; never uploaded |
| Audio-derived features (`outputs/noise/*_levels.csv`) | 1-second A-weighted levels, third-octave bands, a crude speech flag, limiter flags | GitHub (public) | Yes, published | Permanent |
| Positioning and QC outputs (`outputs/`, `outputs_posthoc/`) | Derived sensor features, section timings, tracker results | GitHub (public) | Yes, published | Permanent |
| Ride inventory (`outputs/inventory.md`) | **Ride start and pause times to the millisecond, next to station pairs** | GitHub (public) | Yes, published | Permanent. This identifies the contributor's own journeys; it is published with his consent and would not be published for other contributors (see below). |
| Atlas `documents` | Public TfL / London Assembly documents | MongoDB Atlas (AWS eu-west-1) | Yes | Until the project ends |
| Atlas `section_stats`, `attempts`, `claims`, `approvals` | Summary statistics, the analyst's metrics log, the investigator's claims, approval records | MongoDB Atlas | Yes | Until the project ends |
| Gemini prompts | Public document text, summary statistics, tracker metrics | Google Gemini API | Yes | See below |

## What is never kept or sent

- **Raw audio is never written anywhere but its original file.** It is decoded in memory for each
  analysis; no WAV, cache, spectrogram image or clip is saved. (Scratch analyses during development
  saved coarse spectra and position arrays only, in a temporary job folder.)
- **No speech.** The kept audio features are 1-second levels and third-octave bands. Gontier et al.
  (2017, Sensors 17(12):2758) found that third-octave spectra at 125 ms frames make speech almost
  unintelligible in a listening test; ours are eight times coarser. That study did not test modern
  machine-learning reconstruction, so we state this as tested evidence, not a guarantee.
- **No audio and no raw sensor data are sent to Gemini or Atlas.**

## Gemini

- The API key belongs to a project with a billing account (the API reports its spending cap). For
  users in the UK, the Gemini API terms apply the paid-service data terms to all services: prompts and responses are not used to improve Google's products. Google
  still logs them for a limited period for abuse monitoring, so we do not claim zero retention.
- What we send: public document text, our summary statistics and tracker metrics. Nothing personal.
- We do not use Gemini's Search or Maps grounding with any rider data (grounding data is kept for
  30 days and cannot be opted out of).
- Not built yet: announcement transcription. If built, it would send only windows of at most 8 s
  around station stops, encoded in memory, with storage disabled (`store=false`) and any uploaded file
  deleted straight after; whole rides are never sent.

## MongoDB Atlas

- Holds public documents and derived data only.
- Automated Embedding runs its Voyage model in a US region, so only public document text is embedded;
  claims and anything derived from rides are never embedded.

## If other people contribute rides (the intended product)

- Features are computed on the phone or laptop and raw audio is deleted straight after.
- Each trip gets a random ID; Atlas stores no wall-clock times (at most a service-date band such as
  AM peak), no device model and no OS version.
- A section is published only once at least N trips from several contributors cover it, and single
  trips are never published.
- Contributors give explicit consent, and the recording app keeps the microphone off by default
  outside a ride.
- Differential privacy is not used: at this scale the noise it needs would swamp the dB values.
  Contributor thresholds and coarse timestamps do the job instead.

## Honest limits

- The public inventory does show the contributor's own journey times (see above).
- Speech-flag accuracy in ~90 dB carriage noise has not been measured; it is a data-quality filter,
  not the privacy guarantee. The guarantee is the 1-second aggregation.
- Re-use licences for the TfL and London Assembly documents were not verified; they are quoted for
  citation only.
