// The steering wheel, drawn rather than set in a font.
//
// The nerd font's "steering" glyphs are a lottery — the same codepoint is a
// different picture in the next patched family, and the one that looked right
// here would break the day the theme changed its font. A wheel is a rim, a
// hub and three spokes; a canvas draws exactly that at whatever size the bar
// gives it, in whatever colour the theme is currently using.
//
// Three spokes rather than four or a plain ring: four reads as a ship's helm
// and a ring reads as a record. The hub fills in when the engine is turning,
// which is the same "lit" convention the rest of the bar uses.
import QtQuick

Canvas {
  id: root

  property color tint: "#ffffff"
  // Filled through when the engine is turning, so the bar says "running"
  // without needing a second indicator beside it.
  property bool running: false
  property real spin: 0

  onTintChanged: requestPaint()
  onRunningChanged: requestPaint()
  onSpinChanged: requestPaint()
  // Both dimensions, not just the width. A Canvas is only repainted when it is
  // asked to be, and the bar hands this one its size in two steps -- a width
  // first and a height a frame later. Watching width alone meant the repaint
  // happened while the height was still nought, and the wheel never came back.
  onWidthChanged: requestPaint()
  onHeightChanged: requestPaint()

  onPaint: {
    var ctx = getContext("2d")
    // reset() returns the context's state -- transform, stroke, fill -- to its
    // defaults; it does not erase what was already drawn. Without the clear,
    // a repaint at a new angle or a new size leaves the previous wheel
    // underneath the new one, which at bar sizes reads as a smeared ring.
    ctx.reset()
    ctx.clearRect(0, 0, width, height)
    var s = Math.min(width, height)
    if (s < 4) return
    var r = s / 2
    ctx.translate(width / 2, height / 2)
    ctx.rotate(root.spin)

    var rim = r * 0.94
    ctx.strokeStyle = root.tint
    ctx.fillStyle = root.tint
    ctx.lineCap = "round"
    ctx.lineJoin = "round"

    // The rim. Thin: at sixteen pixels a heavy ring swallows the spokes and
    // the whole thing reads as a filled disc.
    var lw = Math.max(1.1, r * 0.15)
    ctx.lineWidth = lw
    ctx.beginPath()
    ctx.arc(0, 0, rim - lw / 2, 0, Math.PI * 2)
    ctx.stroke()

    // Three spokes: one up, two swept down and out, which is the CR-Z's own
    // wheel and also the arrangement that stays legible when the picture is
    // smaller than a word.
    ctx.lineWidth = Math.max(1.1, r * 0.14)
    var spokes = [-Math.PI / 2, Math.PI * 0.12, Math.PI * 0.88]
    for (var i = 0; i < spokes.length; i++) {
      var a = spokes[i]
      ctx.beginPath()
      ctx.moveTo(Math.cos(a) * r * 0.18, Math.sin(a) * r * 0.18)
      ctx.lineTo(Math.cos(a) * (rim - lw), Math.sin(a) * (rim - lw))
      ctx.stroke()
    }

    // The hub. Solid while the engine is turning, a ring while it is not —
    // the same "lit" convention the rest of the bar uses.
    ctx.beginPath()
    ctx.arc(0, 0, r * 0.30, 0, Math.PI * 2)
    if (root.running) {
      ctx.fill()
    } else {
      ctx.lineWidth = Math.max(1.0, r * 0.13)
      ctx.stroke()
    }
  }
}
