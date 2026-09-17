# UI/UX refinement

This version prioritizes the interactive design. The default Drive view uses an AI-generated gunmetal CR-Z concept with black rims. It is a raster image, not a live 3D model or scan of the actual vehicle. GLB import remains available in Setup.

- Cruise, Regenerate, and Parked demo scenarios change the speed, engine and energy-flow presentation.
- Focus view simplifies the driving surface.
- CarPlay now has interactive Overview, Maps, and Music design previews. Route switching and simulated playback controls do not navigate, play audio, call anyone, or connect to an iPhone.
- Day, Night and automatic clock-based appearance remain available.
- Hardware integration work is paused in favor of UI/UX, per the user's direction.

## Generated asset
Built-in image generation tool, 1536 × 1024 PNG with alpha channel, saved as public/crz-concept.png.

Prompt: Create a photorealistic automotive studio render asset for an exquisite Tesla/Apple inspired in-car tablet dashboard. One 2015 Honda CR-Z facelift sport hybrid compact 2-door hatchback, accurately recognizable CR-Z wedge proportions, gunmetal metallic gray paint, factory-style stock black alloy rims, low-profile tires, Honda nose badge, angular elongated swept headlights, correct 2013-2015 facelift grille. Front three-quarter view with nose facing the left of frame and passenger side extending right, camera slightly elevated so hood and roof visible. Full car uncropped occupying 85% of a wide landscape image, isolated on a genuinely transparent alpha background with subtle soft contact shadow only. Premium physically based automotive visualization, realistic paint flakes, softbox highlights, subtle cool studio reflections, beautifully detailed glass, dark cabin without people, realistic black wheels not aftermarket oversized rims, headlights softly illuminated white. No environment, no landscape, no scenery, no labels, no UI, no text, no watermark. This is a concept visualization, aim for convincing car showroom quality. Landscape 3:2 composition.

## Validation
Production build and TypeScript checks performed. No on-car testing or browser interaction testing was performed. The optional WebMCP theme tool remains unverified in a supported WebMCP context.

## Energy and tablet refinement
- Charge dial, explanation of assist/regeneration/rest, and a selectable recent-power graph. History is collected from current demo readings, not fabricated historical data.
- Improved Surface landscape spacing and view transitions.
- Paused/parked energy-flow animation stops; speed units stay consistent between Drive and Systems.
- Main navigation uses tab panels with keyboard support.

## Automotive layout refinement
- Bottom navigation dock replaces the sidebar.
- Larger centered vehicle and speed display, fewer borders, compact tablet-height treatment, and quieter labels.
- Warm Deep night theme, interface dimmer, gentle motion and speed-unit preferences persist in browser storage.
- Demo scenario readings ease into the new state. This interpolation applies only to demo data.
- CarPlay preview state and battery history persist when switching views within the page.
- Model auto-rotation pauses with the simulation or Gentle motion setting.

## Honda drive modes
- ECON, NORMAL and SPORT have separate preview selection and a persistent mode indicator in the header plus instrument readout.
- Green/blue/red are interface mode accents. Honda's original NORMAL/ECON ambient meter also changes with efficiency; this is an inspired design, not an exact recreation of that logic.
- Assist, regeneration and standby remain separate from the selected drive mode.
- Preview selection never writes to the vehicle. Normal is the prototype's initial mode.
- Live mode accepts only a fresh, explicit `driveMode` string of `econ`, `normal` or `sport` (case-insensitive). Missing, unrecognized, disconnected and stale mode data is shown as unknown. The supplied OBD bridge does not yet decode this Honda signal.
- Mode-aware demo assist levels and battery charge direction are illustrative, not calibrated vehicle physics.
- Verified mode parsing for all three modes, uppercase input, and rejection of missing or unrelated energy-state values. Production build and type checks pass.

Reference: Honda 2015 CR-Z 3-Mode Driving guide
https://owners.honda.com/utility/download?path=%2Fstatic%2Fpdfs%2F2015%2FCR-Z%2F2015_CR-Z_3-Mode_Driving.pdf
Eco Assist behavior:
https://owners.honda.com/utility/download?path=%2Fstatic%2Fpdfs%2F2015%2FCR-Z%2F2015_CR-Z_Eco_Assist_System.pdf

## Coherent instrumentation
- Added a CHRG / ASST segmented display driven by signed simulated motor power.
- NORMAL/ECON gauge lighting shifts between blue and green using a simplified demo efficiency state; SPORT stays red. No real efficiency signal is inferred in live mode.
- Regeneration preview decelerates toward zero and transitions to rest. Battery charge follows displayed motor power direction. Parked mode settles to zero speed, RPM, and motor power.
- Demo behavior is deliberately illustrative; it is not calibrated vehicle physics or a diagnostic substitute.
- Checks cover regeneration stopping, battery charge direction, mode-dependent assist, state-of-charge bounds, and the parked state.

## Precision and appearance polish
- Added large day, night, deep-night and automatic palette cards.
- Added brief ECON/NORMAL/SPORT selection feedback and consistent 44-pixel touch targets.
- Battery power now uses signed assist/regeneration areas, 30/60-second windows, observed peaks and explicit no-reading states.
- Mini charts use sampled cockpit readings. Missing values and long sampling gaps break the trace rather than inventing a connection.
- Battery-to-motor flow follows power direction and stops at rest; motion preferences are respected.
- Source switches clear history, while old socket callbacks cannot clear newly resumed demo readings.
- Checks passed for signal gaps, missing values, zero-power samples, regeneration and parked transitions. Type checking and the production build passed. Local preview returned HTTP 200. Browser interaction and visual QA were not performed.
- The preview runs from persistent local storage outside iCloud to avoid repeated file offloading. The editable project and this source bundle contain the same application changes.
