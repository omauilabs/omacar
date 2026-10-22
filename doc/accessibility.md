# Accessibility

OmaCar is built to WCAG 2.2 AA on every screen, and reaches AAA where a driver
needs it most: with OmaAccess's high contrast on, every text colour clears 7:1,
and every control is a touch target of 44 px or more. It follows the person's
accessibility settings from OmaAccess, the Omarchy accessibility plugin, so
they are set once for the whole tablet.

## OmaAccess and OmaCar

OmaAccess (omauilabs/omaaccess) owns the settings. OmaCar reads them, and the
Accessibility rows in Settings change them through OmaAccess, so the desktop,
OmaAccess's panel and the dashboard always agree. Without OmaAccess (a Mac, a
fresh tablet), the same rows keep OmaCar's own settings in
`~/.config/omacar/access.json`.

| OmaAccess setting | What OmaCar does |
|---|---|
| Text size (`textSize`) | The whole dashboard scales with it, on top of OmaCar's own size: 100% to 200%. |
| High contrast (`contrast`) | Secondary text becomes primary, panels go solid, edges become visible lines, and the status red and violet lighten. Every text colour is then 7:1 or better (AAA). |
| Reduce motion (`still`) | Nothing animates or slides. The system's own reduce-motion setting does the same. |
| Dyslexia font (`dyslexiaFont`) | The dashboard uses OpenDyslexic too, with wider letter, word and line spacing. |
| Large targets (`bigTargets`) | Every button grows to at least 64 px. |
| Flash for sounds (`alerts`) | Every sound OmaCar makes also glows the screen's edge. At most once every 2 s: never a strobe (WCAG 2.3.1). |
| Captions (`captions`) | Every chime, alarm and spoken line is also shown in words. |
| Read alerts aloud (`speakAlerts`) | Alerts are spoken in OmaAccess's voice (Piper when it is set up). |
| Screen reader (`sr`, Orca) | OmaCar stops speaking for itself, because Orca reads the page's live regions. Two voices at once help nobody. |
| Colour filters, invert, zoom, magnifier | OmaAccess draws these in the compositor, under the whole screen. OmaCar needs to do nothing, and does nothing. |
| Language (`language`) | Sets the page's language, so a screen reader pronounces it correctly. |

How OmaCar reads them:

- `lib/access.py` runs `omaaccess status --json`, which gives the effective
  state after profiles and defaults.
- It runs it again only when `~/.config/omaaccess/state` changes (OmaAccess has
  no D-Bus signal; that file is its only change signal), or every 30 s.
- The page polls `/api/access` every 5 s, so a change made in OmaAccess's panel
  appears on the dashboard within seconds, with no reload.

From the terminal:

```
omacar access                    # what is in force, and where it comes from
omacar access set contrast on    # through OmaAccess if installed
```

## Speech and quiet hours

OmaCar speaks only when the person asked for it ("Read alerts aloud"), only
when no screen reader is running, and never overnight with the car off unless
it is a safety alert (the child alert). A child sleeps near the tablet; that
rule is lib/intent.py's quiet hours, the same one the voice assistant obeys.
`test/access_test.py` holds all four.

The remote cockpit can neither change these settings nor make the tablet
speak: neither route is in serve.py's `COCKPIT_WRITES`.

## What a screen reader gets

- **One heading per screen.** Every screen has a level-one heading, its title,
  and focus moves to it when the screen opens, so the screen reader says where
  you are.
- **Live regions.** `say()` in share/js/a11y.js speaks through two regions in
  app.html: a polite one, and an urgent one for alarms and the child alert.
- **Every sound in words.** Every chime and alarm is also announced in words,
  so a blind driver knows which sound is OmaCar's.
- **Dialogs.** Every dialog is named by its title, Tab stays inside it, and
  focus returns to whatever opened it. A dangerous confirm starts with focus on
  Cancel.
- **Cards that open screens.** A card on Home or the car apps page is not
  itself a button, because it can hold buttons of its own. A real button lies
  over it for the keyboard and the reader.
- **Custom buttons.** Space presses them, as it presses a real button.

## Measured

Every result below comes from axe-core 4.10 on all 46 screens of the
integrated build (1368×912), on 2026-10-17. All animations were finished
before each check, because a fade half done is not the colour a person reads.

| Check | Before | After |
|---|---|---|
| WCAG 2.2 A + AA + best practice (axe) | 6 rules, 135 elements | 0 |
| AAA contrast 7:1, default look | not measured | 51 elements between 4.76 and 6.75:1 (all pass AA) |
| AAA contrast 7:1, high contrast on | not measured | 0 |
| Touch targets ≥ 44 px (AAA 2.5.5), 1,094 controls, tablet density | ~40 under | 0 |
| Text clipped on Home at 150% / 200% | yes | 0 / 0 |

The default look keeps its quieter caption tone (`--faint`, 5.35:1) on
purpose: it is the visual hierarchy the dashboard is designed around, and it
passes AA. A person who needs more turns on high contrast, and gets AAA.

Large text (WCAG 1.4.4, AA): at 20 px and up (OmaCar's Huge, or OmaAccess at
130% and more), Home's fixed grid lets its rows grow and the screen scrolls.
Measured at 150% and 200%: no text on Home ends outside its card. Before
this, 150% clipped the Phone integration card.

Not measured yet, and owed:

- Orca reading the real dashboard on the Surface, with real speech.
- OmaAccess on the tablet changing a setting and the dashboard following it.
  The tests here check that mapping against OmaAccess's documented JSON, not a
  running OmaAccess.
- `omaaccess set textSize 18` is assumed from OmaAccess's README; it has not
  yet been run against OmaAccess itself.
