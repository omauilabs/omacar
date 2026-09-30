// The meetup demo's entry point (doc/design/2026-09-30-meetup-demo.md).
//
// Loaded only by demo/demo.html, and before js/main.js, so the door main.js
// reads once at load (globalThis.OMACAR_DEMO) is open by then. The shape:
//
//   views:      { [viewId]: mount | { mount, fast } }             replaces a live view's mount;
//               mount(root, {arg}) -> unmount|null, fast = poll /api/live at 4 Hz
//   extraViews: [{ id, label, title, mount, fast }]               the demo's own routable screens
//   tabRoots:   { [tabId]: viewId }                               the screen a tab opens on
//   cards:      { [cardId]: () -> { node, paint(), destroy() } }   dresses an existing Home card
//   afterBar:   (vbar) -> void                                    after every paint of the top bar
//   onKey:      (KeyboardEvent) -> boolean                        true when the demo used the key
//
// Each part of the demo registers itself here from its own module; this file
// only wires them (Task 8 of doc/design/2026-09-30-meetup-demo-plan.md).
globalThis.OMACAR_DEMO = {
  views: {}, extraViews: [], tabRoots: {}, cards: {}, afterBar: null, onKey: null,
};
