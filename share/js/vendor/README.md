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
