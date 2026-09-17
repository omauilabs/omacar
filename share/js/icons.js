// Icon paths, shared.
//
// These lived in main.js, which was fine until a second consumer appeared. The
// hub imports them and main.js imports the hub, so leaving them in main.js
// would have made the two files import each other -- and an ES module cycle
// fails at load time as an undefined binding, not as a clear error.
export const ICONS = {
  dash: ["M3 12.5 12 4l9 8.5", "M5.5 10.6V20h13v-9.4"],
  scan: ["M3 7V4h3", "M21 7V4h-3", "M3 17v3h3", "M21 17v3h-3", "M7 12h10"],
  codes: ["M12 3.5 21 20H3z", "M12 10v4", "M12 17.2v.1"],
  data: ["M3 17l4-7 3.5 4L15 6l6 11", "M3 20h18"],
  health: ["M12 21s-7.5-4.7-7.5-10A4.5 4.5 0 0 1 12 7.6 4.5 4.5 0 0 1 19.5 11c0 5.3-7.5 10-7.5 10z"],
  concerns: ["M3.5 17.5 9 11l4 3.6 7.5-8.6", "M15.5 6h5v5"],
  service: ["M14.7 6.3a4 4 0 0 0 5 5L15 16l-3 3-3-3 4.7-4.7a4 4 0 0 0-5-5L12 3l3 3z"],
  history: ["M3.5 12a8.5 8.5 0 1 0 2.6-6.1", "M3 4v5h5", "M12 8v4.4l3 1.8"],
  tests: ["M5 12h3.2", "M15.8 12H19", "M12 5.2v13.6", "M8.2 8.6a5.4 5.4 0 0 0 0 6.8",
          "M15.8 8.6a5.4 5.4 0 0 1 0 6.8"],
  advisor: ["M12 3.2v3.1", "M12 17.7v3.1", "M4.6 7.6l2.7 1.5", "M16.7 14.9l2.7 1.5",
            "M4.6 16.4l2.7-1.5", "M16.7 9.1l2.7-1.5", "M12 9.4a2.6 2.6 0 1 0 0 5.2 2.6 2.6 0 0 0 0-5.2z"],
  // A sun and a moon, for the day/night toggle in the vehicle bar. The
  // `advisor` glyph above is ALSO a disc with rays, which is most of why
  // somebody tapped it expecting the lights to change and got the assistant.
  // These two are drawn to be told apart at arm's length in a moving car: the
  // sun keeps its rays, the moon is an unmistakable crescent with none.
  // A SUNRISE, NOT A SUN. The first version of this was a disc with eight
  // symmetric rays -- which is very nearly the `advisor` glyph above, and the
  // two then sat side by side in the vehicle bar being mistaken for each
  // other. That is the whole complaint this control was added to answer, so
  // repeating it one button along would have been funny rather than useful.
  //
  // A half-disc sitting on a horizon cannot be read as a full disc at arm's
  // length, in a car, at a glance -- which is the only test that matters.
  sun: ["M3.4 18.4h17.2",
        "M6.6 14.6a5.4 5.4 0 0 1 10.8 0",
        "M12 4.2v2.6", "M4.9 7.4l1.8 1.8", "M19.1 7.4l-1.8 1.8"],
  moon: ["M20.1 14.6A8.6 8.6 0 0 1 9.4 3.9a8.6 8.6 0 1 0 10.7 10.7z"],
  report: ["M6.5 3h7.5l4 4v14h-11.5z", "M14 3v4.5h4", "M9 12.5h6", "M9 16h6"],
  live: ["M12 3a9 9 0 1 0 9 9", "M12 12l5-5"],
  themes: ["M12 3.4a8.6 8.6 0 1 0 0 17.2c1.3 0 2-.8 2-1.8 0-.5-.2-.9-.5-1.2-.3-.3-.5-.7-.5-1.1 0-1 .8-1.8 1.8-1.8h1.6a4.5 4.5 0 0 0 4.2-4.6c0-3.9-3.9-6.7-8.6-6.7z",
           "M7.6 12.2v.1", "M9.9 8.2v.1", "M14.1 8.2v.1", "M16.4 11.4v.1"],
  documents: ["M6.5 3h7.5l4 4v14h-11.5z", "M14 3v4.5h4",
              "M9 12h6", "M9 15.5h6", "M9 19h3"],
  replay: ["M4 12a8 8 0 1 0 2.3-5.6", "M4 4v4.5h4.5", "M10.5 9.4l4.6 2.6-4.6 2.6z"],
  resets: ["M12 4.5v3.2", "M12 16.3v3.2", "M4.5 12h3.2", "M16.3 12h3.2",
           "M12 8.6a3.4 3.4 0 1 0 0 6.8 3.4 3.4 0 0 0 0-6.8z"],
  learn: ["M12 6.5C10.4 5.2 8.4 4.6 6 4.8v12c2.4-.2 4.4.4 6 1.7 1.6-1.3 3.6-1.9 6-1.7v-12c-2.4-.2-4.4.4-6 1.7z",
          "M12 6.5v12"],
  garage: ["M3 10.5 12 5l9 5.5", "M5 10v9h14v-9", "M8.5 19v-4.5h7V19"],
  hub: ["M4 5.5h6.2v5.6H4z", "M13.8 5.5H20v5.6h-6.2z",
        "M4 12.9h6.2v5.6H4z", "M13.8 12.9H20v5.6h-6.2z"],
  drive: ["M4.5 13.5 6.2 8.4A2 2 0 0 1 8.1 7h7.8a2 2 0 0 1 1.9 1.4l1.7 5.1",
          "M4.5 13.5h15v3.8h-3v-1.6h-9v1.6h-3z", "M7.4 15.6h.1", "M16.5 15.6h.1"],
};
