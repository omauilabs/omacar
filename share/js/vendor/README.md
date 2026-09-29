# Vendored d3

`d3.min.js` is a single self-contained ES module — a subset of d3 7.x, bundled
with esbuild. It is checked in rather than fetched, because a car app has to
draw its charts in a parking garage with no signal.

## What is in it

Only the modules the charts actually use:

    d3-selection  d3-scale  d3-scale-chromatic  d3-shape  d3-array  d3-axis
    d3-format     d3-time   d3-time-format      d3-transition
    d3-interpolate d3-color d3-ease  d3-path  d3-polygon  d3-contour

That is 160 KB against ~280 KB for the full build. The omissions are
deliberate: no d3-geo (no maps), no d3-hierarchy (no treemaps), no d3-force,
no d3-fetch (the app has its own `api()`), no d3-dsv (nothing parses CSV in
the browser), no d3-zoom/drag/brush (the charts are read-only on a screen
that is often a touchscreen in a moving car).

Add a module by putting it in `entry.js` and rebuilding — do not reach for a
CDN, and do not import a second copy of a d3 submodule alongside this bundle.

## Rebuilding

    npm install d3-selection d3-scale ... esbuild
    esbuild entry.js --bundle --format=esm --minify --legal-comments=none \
      --outfile=d3.min.js

where `entry.js` re-exports each module above.

d3 is ISC licensed, and ISC requires the copyright and permission notice to
appear in all copies — a bundle included. So the notice is prepended to
`d3.min.js` by hand after every rebuild, and the full text sits in `LICENSE-d3`
beside it. `--legal-comments=none` strips esbuild's own banner handling, which
is why this is a manual step rather than a flag.

# MediaPipe, fetched rather than vendored

`mediapipe/` holds drowsy mode's face tracker: MediaPipe tasks-vision 1.0.1
(`vision_bundle.mjs` and the SIMD `wasm/` pair) and the Face Landmarker model
(`face_landmarker.task`, float16 v1). It is not in git. `omacar assets fetch`
downloads it once, at install, from the URLs in `share/assets/manifest.json`
(`fetch`), and holds every file to the size and SHA-256 pinned there. The app
never fetches it.

The no-SIMD pair tasks-vision also ships is not fetched: Chromium on the
tablet has WebAssembly SIMD, and `share/js/facewatch.js` names the SIMD files
directly.

tasks-vision is Apache License 2.0, as its package.json declares; the model is
Google's, distributed with MediaPipe under Apache 2.0 per its model card.

## Restoring offline

With no signal, install from the backup copy kept on the Omarchy box at
`~/Projects/.omacar-vendor-backup/mediapipe-1.0.1/` (four files, flat, with a
`SHA256SUMS` alongside them) instead of downloading:

    omacar assets fetch --from ~/Projects/.omacar-vendor-backup/mediapipe-1.0.1/

Each file is still checked against the pin in `share/assets/manifest.json`; a
copy that does not match is refused exactly as a bad download would be.
