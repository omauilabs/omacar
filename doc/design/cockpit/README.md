# CR-Z Omarchy cockpit

Current focus: UI/UX prototype. See DESIGN-NOTES.md for the latest interactive design changes.

## Delivered software
- Touch-oriented Surface Pro 7 dashboard with Drive, Energy, Systems, CarPlay and Display views.
- Day, night, warm deep night, and clock-based automatic theme; saved per browser.
- Clearly marked simulated telemetry with pause/resume.
- Live local WebSocket input, null handling, and three-second stale-data clearing.
- Self-contained GLB import with orbit, zoom, studio lighting, and auto-rotation. No finished custom CR-Z GLB is included.
- Read-only serial ELM327 bridge for standard speed, RPM, coolant and adapter voltage. Not hardware-tested.

## What is still needed
- OBDLink EX USB is confirmed. Its Linux serial-device detection, access permissions, and connection to the CR-Z still need testing on the Surface.
- On-car validation of supported readings. Honda IMA pack charge, temperature, voltage and motor power require a verified vehicle-specific profile. They remain blank in live mode.
- A properly licensed 2015 CR-Z GLB with gunmetal paint and the correct black stock wheels. The default centerpiece is an AI-generated gunmetal studio concept, not the user's actual vehicle or a live 3D model. A 2014 reference photo is also retained in the bundle.
- Local CarPlay host setup and physical USB/audio/microphone/touch testing. The CarPlay view is an interactive Maps/Music design preview; it does not implement a CarPlay receiver or embed a working stream.

## Surface / Omarchy setup
The Surface Pro 7 Intel is the target, but this session runs on a Mac and cannot install or test the Surface. Check Linux Surface's feature matrix and touchscreen setup before mounting it:
https://github.com/linux-surface/linux-surface/wiki/Supported-Devices-and-Features
https://github.com/linux-surface/linux-surface/wiki/Installation-and-Setup

Run the included dashboard source on the Surface with `npm ci` then `npm run dev -- --host 127.0.0.1` for an initial stationary test. Open http://localhost:3000 in Chromium. A deployment/kiosk service should be configured after hardware validation, not inferred from this prototype.

For your OBDLink EX USB cable, identify its actual serial path under `/dev/serial/by-id/` after connecting it. Prefer that stable path over `/dev/ttyUSB0`, especially with the Carlinkit dongle also attached. Create a Python virtual environment, install `hardware/requirements.txt`, then run:
`python hardware/obd_bridge.py --port /dev/ttyUSB0`
Use your actual device path. The bridge auto-detects serial baud rate; if necessary, specify the adapter’s current rate using `--baudrate 115200`. Do not change adapter firmware or assume its rate if other software has reconfigured it. On Omarchy, inspect device ownership with `ls -l /dev/ttyUSB*` and grant the user access through the appropriate device group/rule; do not run the dashboard as root. OBDLink officially lists Android and Windows for EX, so Linux operation remains an integration to validate, not an official compatibility claim.

In Setup, choose Connect local OBD2 bridge. No diagnostic writes, code clearing, actuator commands or guessed Honda PID requests are sent. ELM327 initialization is performed by python-OBD. The adapter's voltage reading is not a traction-battery reading.

The bridge binds only to loopback and restricts WebSocket origins. Prefer the locally served app; a hosted HTTPS page may block or prompt for loopback access. No car telemetry is uploaded to the hosted Site. No automatic fallback to demo occurs after connection loss.

## CarPlay route
CPC200-CCPA is explicitly listed by the community Linux CarPlay projects below. The official AutoKit app targets Android, so Linux requires a separate community host. Verify the project, dependencies, firmware, audio and your iPhone before deployment:
https://github.com/rhysmorgan134/node-CarPlay
https://github.com/rhysmorgan134/react-carplay
https://www.carlinkit.com/products_detailed/9256781.html

## 3D asset
Free CR-Z model (3ds Max format, conversion required):
https://www.viz-people.com/portfolio/free-3d-model-honda-cr-z/
Paid portable model candidate (not purchased):
https://3dmodels.org/3d-models/honda-cr-z/
A converted and optimized self-contained GLB can be loaded in Setup. Imported files stay local to the browser and must be reloaded after a page refresh.

## Image attribution
2014 Honda CR-Z Sport-T i-VTEC 1.5 Front, Vauxford, CC BY-SA 4.0.
https://commons.wikimedia.org/wiki/File:2014_Honda_CR-Z_Sport-T_i-VTEC_1.5_Front.jpg
https://creativecommons.org/licenses/by-sa/4.0/
Original file included; the interface crops and desaturates it for display.

## Technical references
https://python-obd.readthedocs.io/en/latest/Command%20Tables/
https://python-obd.readthedocs.io/en/latest/Connections/
https://automobiles.honda.com/images/2015/cr-z/downloads/2015-cr-z-brochure.pdf

## OBDLink EX references
https://www.obdlink.com/products/obdlink-ex/
https://support.obdlink.com/support/solutions/articles/43000733076-troubleshoot-obdlink-issues-after-using-other-obd-apps
https://support.obdlink.com/support/solutions/articles/43000705533
The EX supports standard OBD-II on compatible Hondas despite its Ford focus. OBDLink’s Honda enhanced diagnostic app add-on is exclusive to MX+; the EX cable alone does not provide the needed Honda IMA decoding profile.
