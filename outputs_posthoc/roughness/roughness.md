# TfL 2018 rail roughness plots, Bethnal Green -> Mile End eastbound (FOI-1730-1819)

**POST-HOC. Argon-extracted, deterministically checked, pending human confirmation (`HUMAN_CHECK.md`).** gemini-4-argon, thinking medium, 2 independent calls on the PDF alone; checks and pixel digitizer in `agent/roughness.py`; full record in `roughness.json`. Extraction time (latest run) 2026-10-25T04:19:42+00:00. The reading, anchor and summary checks below were added after the first two runs, on review, and applied to the saved runs.

Source: TfL, *Rail Roughness Plots BEG-MIE January 2018* (FOI-1730-1819, published 2018-10-22), `data/corpus/files/FOI-1730-1819__1.pdf`. 2 pages, 4 figures. The PDF text layer holds only the title and the 4 captions; every axis, number and trace is inside 4 embedded raster images (1600x598 px).

Each figure: a header line (`Main Av P-P`, `Exceedence` above a threshold); an upper panel, moving-average peak-to-peak roughness in the caption's wavelength band (µm) against Position (m), with a dashed threshold line; a lower panel, a roughness spectrum (dB rel 1 µm RMS) in third-octave wavelength bands from 2000 mm to 3.15 mm.

## TfL's own statement about this site

TfL's response to the same FOI request (corpus chunk `FOI-1730-1819#000`, whitespace normalised):

> The rail roughness measurements confirm an elevated rail roughness levels between 720 and 880m which are the likely cause of the noise at this site.

> We will undertake rail grinding at this location in February 2019, with the intention to improve the wheel-rail interface.

The causal claim ("likely cause of the noise at this site") is TfL's, not ours: we did not link roughness to noise. This is the only reference in the loop that Claude did not write.

Checks against it: run 1: a zone passing the zone rule overlaps it in all four figures; run 2: a zone passing the zone rule overlaps it in all four figures.

A stretch is confirmed (both runs, zone rule) in all four figures, but its ends differ by rail: Fig 1 starts at 720-735 m and ends at 1210-1220 m; Fig 2 starts at 720-735 m and ends at 1200 m; Fig 3 starts at 725 m and ends at 905 m; Fig 4 starts at 725 m and ends at 905-910 m. The overlap common to all four is about 735-905 m (union of the four: 720-1220 m, not elevated everywhere; overlaps TfL's 720-880 m: yes).

Model-free pixel check, per figure (onset = first stretch >= 100 m where the 10 m median envelope stays above 2 x the printed threshold; median = envelope median inside 720-880 m vs elsewhere): Fig 1: onset 725 m, median 73.3 vs 8.1 µm; Fig 2: onset 732 m, median 135.1 vs 28.1 µm; Fig 3: onset 729 m, median 28.7 vs 6.9 µm; Fig 4: onset 729 m, median 149.0 vs 24.4 µm.

## What the plots show (only what survived every check)

- **Fig 1 (LHR 30-100mm, p.1)**: header `Main Av P-P: 16.5µm Exceedence: 41% (0.626km) >10µm`. Tallest spike 136 µm at 865 m (digitizer; Argon runs 869 m / 135 µm, 883 m / 135 µm [fails the pixel check]). Stretch above the threshold line reported by both runs and passing the zone rule: 720-735 to 1210-1220 m (over runs; envelope median 42.8 µm inside vs 6.2 µm elsewhere).
- **Fig 2 (LHR 100-300mm, p.1)**: header `Main Av P-P: 39.2µm Exceedence: 41% (0.626km) >30µm`. Tallest spike 327 µm at 170 m (digitizer; Argon runs 169 m / 325 µm, 168 m / 324 µm). Stretch above the threshold line reported by both runs and passing the zone rule: 720-735 to 1200 m (over runs; envelope median 87.9 µm inside vs 21.8 µm elsewhere).
- **Fig 3 (RHR 30-100mm, p.2)**: header `Main Av P-P: 10.9µm Exceedence: 33% (0.504km) >10µm`. Tallest spike 112 µm at 169 and 264 m (digitizer; Argon runs 262 m / 112 µm, 265 m / 111 µm). Stretch above the threshold line reported by both runs and passing the zone rule: 85-95 to 275 m (over runs; envelope median 18.2 µm inside vs 6.9 µm elsewhere); 725 to 905 m (over runs; envelope median 28.4 µm inside vs 6.9 µm elsewhere); 1285-1295 to 1505-1510 m (over runs; envelope median 27.9 µm inside vs 6.9 µm elsewhere).
- **Fig 4 (RHR 100-300mm, p.2)**: header `Main Av P-P: 35.7µm Exceedence: 34% (0.519km) >30µm`. Tallest spike 382 µm at 887 m (digitizer; Argon runs 886 m / 378 µm, 884 m / 385 µm). Stretch above the threshold line reported by both runs and passing the zone rule: 725 to 905-910 m (over runs; envelope median 141.6 µm inside vs 24.4 µm elsewhere); 1290-1295 to 1520-1525 m (over runs; envelope median 55.5 µm inside vs 26.2 µm elsewhere).

- **LHR spectrum** (p.1; the same image in Figs 1 and 2): local peaks found by the digitizer and by every run: 125 mm (23.0 dB, 1.8 dB above both neighbours), 12.5 mm (1.7 dB, 4.7 dB above both neighbours). Digitizer only: 25.0 mm.
- **RHR spectrum** (p.2; the same image in Figs 3 and 4): local peaks found by the digitizer and by every run: 200 mm (24.3 dB, 1.8 dB above both neighbours), 25.0 mm (1.3 dB, 1.5 dB above both neighbours), 12.5 mm (-3.9 dB, 1.8 dB above both neighbours).

In plain words: in January 2018 one stretch of this eastbound section sat above the plotted threshold line (>10 µm in the 30-100 mm band, >30 µm in 100-300 mm, per the headers) in all four figures (both rails, both wavelength bands). Argon read it as starting at about 720-735 m and ending at about 905-1220 m of TfL's plotted position, depending on rail and band (bounds as read by Argon; the zone rule confirms that the trace inside is elevated, it does not check the bounds); the model-free pixel onset, where the 10 m median stays above twice the threshold, is 725-732 m. TfL's own response to the same request states elevated rail roughness between 720 and 880 m; the stretch above overlaps it. The LHR spectrum has small local bumps at 125 mm (+1.8 dB), 12.5 mm (+4.7 dB) (level above both neighbouring bands). The RHR spectrum has small local bumps at 200 mm (+1.8 dB), 25.0 mm (+1.5 dB), 12.5 mm (+1.8 dB) (level above both neighbouring bands).

## Checks

| Check | Run 1 | Run 2 |
|---|---|---|
| Counts: 2 pages, 4 captions, 4 images, 4 figures; each figure on its caption's page | pass | pass |
| Captions found in pdfplumber page text (after case, whitespace, dash normalisation) | pass | pass |
| Document title found in page-1 text | **1 fail** | **1 fail** |
| Rail/band labels in the figure's own caption; position axis names the band | pass | pass |
| Axis titles and extreme tick labels = printed | pass | pass |
| Every reading / band / zone / threshold inside its axes | pass | pass |
| Header % x length = header km; header numbers in the header text | pass | pass |
| Header exceedance % inside the plotted trace's bounds (digitizer) | pass | pass |
| Rough zones pass the zone rule (rule fixed before the first run) | **9 fail** | **5 fail** |
| Every global/local maximum Argon reports matches the pixels (±10 m window; spectrum at the band) | **3 fail** | **5 fail** |
| A zone passing the zone rule overlaps TfL's stated 720-880 m in every figure | pass | pass |
| Summary prose not rejected (banned phrasing, identified unlabelled line, claim the pixels contradict). Never 'verified' | **2 rejected** | **2 rejected** |
| Unlabelled lines named by their colour per panel; no wrong colour words | **3 fail** | **2 fail** |

- Axis titles found in the PDF text layer: 0/16 (they are raster), so they are compared with the printed titles transcribed in the code.
- Tolerances fixed before any run: agree <= 5% of the axis span, tight <= 2% (31 m, 1.56 dB: coarse against the 1.5-1.8 dB peaks reported, so absolute errors are given instead); text exact after case, whitespace, dash and µ normalisation. Zone rule: envelope median inside > threshold and >= 2x the median outside.
- Run vs run, each distinct item once (absolute errors): 1v2: text 8/8, header numbers 16/16; 4 global maxima |Δx| median 2.5 / max 14 m, |Δy| median 1 / max 7 µm; 58 distinct band readings |Δ| median 0.3 / max 1.1 dB (0 differ by more than 1.5 dB, the size of the smallest peak prominence reported); zones: 12 matched (boundaries differ median 7.5 / max 235 m), 4 reported by one run only.
- Argon vs the model-free pixel digitizer, per run: run 1: 4 global maxima |Δx| median 1.45 / max 3.9 m, |Δy| median 1.8 / max 3.7 µm; 58 distinct band readings (29 LHR + 29 RHR) |Δ| median 0.3 / max 1.2 dB, 0 over 1.5 dB (LHR median 0.5 / max 1.2 dB; RHR median 0.2 / max 1.2 dB); run 2: 4 global maxima |Δx| median 2.4 / max 17.9 m, |Δy| median 2.3 / max 3.3 µm; 58 distinct band readings (29 LHR + 29 RHR) |Δ| median 0.35 / max 1.4 dB, 0 over 1.5 dB (LHR median 0.4 / max 1.4 dB; RHR median 0.3 / max 1.1 dB).
- Fig 2 and Fig 4 print the same spectrum image as Fig 1 and Fig 3 (pixel-identical lower halves: Fig 1 vs 2 yes, Fig 3 vs 4 yes), so those band levels are counted once, not twice. run 1: Argon's copies differ by at most 0.0 dB (Fig 2 vs Fig 1), 0.0 dB (Fig 4 vs Fig 3); run 2: Argon's copies differ by at most 0.0 dB (Fig 2 vs Fig 1), 0.0 dB (Fig 4 vs Fig 3). Identical values are consistent with Argon copying the numbers across; they are not an independent re-read.
- Every position and spectrum maximum Argon reported, against the pixels (±10 m window, <= 5 % of the span): run 1 41/44 pass; run 2 39/44 pass. Readings on the shoulder of a taller spike within ±10 m fail by construction.
- Run-vs-run agreement cannot catch an error both runs make. Found: Fig 4: run 1 reads 318 µm at 203 m and run 2 reads 324 µm at 197 m; the pixels give at most 238.7 µm within ±10 m. Both runs agree with each other there, and the pixels contradict both.
- Claude's independent visual read (`reference_points.json`, recorded before any Gemini call; 4 header lines + 6 points): vs digitizer 6/6 agree; vs run1 10/10 agree; vs run2 10/10 agree.
- Digitizer calibration: tick marks found = printed labels on every axis; interior residual max 0.54 px, edge labels within 0.98 px. Share of trace columns wholly / partly above the printed threshold: Fig 1 [34.0, 49.2] %, Fig 2 [27.1, 53.3] %, Fig 3 [22.8, 42.6] %, Fig 4 [19.5, 48.0] % (the header % lies inside each).

Rejected or unconfirmed Argon output (kept in `roughness.json`, not used above). Argon's summary prose is never marked verified: 'unverified' means no check rejected it, not that it is right.

- Run 1: 9 zones fail the zone rule (Fig 1 170-315 m, Fig 1 510-655 m, Fig 1 1350-1500 m, Fig 2 160-265 m, Fig 2 370-650 m, Fig 2 1355-1490 m, Fig 4 120-280 m, Fig 4 400-530 m, Fig 4 940-1240 m); 3 of 44 maxima readings fail the pixel check (Fig 1 local_max 72 µm at 1138 m, pixels 80.1, Fig 3 local_max 98 µm at 243 m, pixels 87.5, Fig 4 local_max 318 µm at 203 m, pixels 238.7); 2 of 6 summary sentences rejected, 4 unverified [contradicted by the pixels: ~318 µm at ~203 m (fig 4: 238.7 µm within ±10 m)] [contradicted by the pixels: RHR curve is 0.5–5.5 dB higher than the LHR from 2000 mm to 160 mm (RHR - LHR over 12 bands: -0.9 to +5.3 dB)]; other: document_title is not verbatim page-1 text; unlabelled elements: the grey line (128, 128, 128) on the spectrum panel is not named by its colour in an element about that panel; unlabelled elements: the pink line (255, 204, 204) on the position panel is not named by its colour in an element about that panel; unlabelled elements: vertical lines on the position panel are called green/red but the pixels hold only pink (255, 204, 204).
- Run 2: 5 zones fail the zone rule (Fig 1 165-285 m, Fig 2 160-260 m, Fig 2 605-645 m, Fig 4 125-285 m, Fig 4 945-1240 m); 5 of 44 maxima readings fail the pixel check (Fig 1 global_max 135 µm at 883 m, pixels 103.7, Fig 1 local_max 106 µm at 816 m, pixels 97.2, Fig 1 local_max 101 µm at 800 m, pixels 110.9, Fig 4 local_max 341 µm at 891 m, pixels 381.7, Fig 4 local_max 324 µm at 197 m, pixels 238.7); 2 of 6 summary sentences rejected, 4 unverified [contradicted by the pixels: ~135 µm at ~883 m (fig 1: 103.7 µm within ±10 m)] [contradicted by the pixels: RHR is 1-5 dB higher than LHR from 2000 mm down to 200 mm (RHR - LHR over 11 bands: -0.1 to +5.3 dB)]; other: document_title is not verbatim page-1 text; unlabelled elements: the grey line (128, 128, 128) on the spectrum panel is not named by its colour in an element about that panel; unlabelled elements: vertical lines on the position panel are called green/red but the pixels hold only pink (255, 204, 204).
- Argon's descriptions of the unlabelled lines are reported only where they pass the pixel-colour check; the pixel colours are grey, magenta and olive (spectrum lines) and pink (vertical lines on the position panels), and the PDF identifies none of them. Marker shape is not checked (Claude's read: hollow circles).

## Limits

- A January 2018 measurement, eastbound Bethnal Green to Mile End. TfL's response (same FOI) planned grinding at this location for February 2019; whether it happened is not in our corpus. TfL's answer to MQ 2022/1791 lists Mile End to Bethnal Green (and Bethnal Green to Liverpool Street) among the sections that "benefited from rail grinding to help reduce noise levels (December 2021-May 2022)". The plots say nothing about the rail after January 2018.
- These are TfL's own rail roughness measurements, read off a picture of a plot. We did not measure corrugation, and we did not link a roughness value to a noise level: that link ("likely cause of the noise at this site") is TfL's statement.
- 'Elevated' here means above the plotted threshold line (>10 µm in the 30-100 mm band, >30 µm in 100-300 mm, per the headers); the PDF does not say what the lines mean.
- Position (m) is TfL's plotted position (-4.6 to 1534.0 m LHR, 0 to 1544.5 m RHR); its zero point is not stated, so it is not mapped onto our track bins (TfL's WTT section length is 1,640 m).
- Not explained in the PDF: what LHR/RHR stand for (conventionally left/right-hand rail), the grey, magenta and olive spectrum lines, the pink vertical lines, and why Figs 1 and 2 print the same exceedance (41%, 0.626 km) for different thresholds.
- Argon's summary prose is not verified: only the claims of the form 'V µm at X m', 'peak of V dB at B mm' and 'a-b dB higher than ... from X mm to Y mm' are tested against the pixels; the rest (bounds of stretches, comparisons, shapes) is unchecked.
- Status: **pending human confirmation**. Nothing here goes into the evidence pack until `HUMAN_CHECK.md` is filled in.
