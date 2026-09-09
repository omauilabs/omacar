// The command board: the reference picture, with every command on it live.
//
// WHAT IT IS.
//
// The wallpaper is a picture, and a picture takes no input. This is the same
// picture drawn again on a layer-shell surface that sits between the wallpaper
// and your windows, with one invisible target over each command and each
// header button — `omacar cheatsheet` writes those coordinates out when it
// draws. Hovering one lifts it; pressing one runs it.
//
// WHY IT DRAWS THE PICTURE RATHER THAN LYING OVER IT.
//
// Being transparent and trusting the wallpaper underneath was nearly right and
// had one flaw with no fix: the wallpaper is drawn by another surface, and two
// surfaces on the same layer have no defined order. Ours could sit under it,
// which puts every target beneath the thing it is meant to be on top of.
// Drawing the same PNG removes the question — there is still exactly one
// rendering, and now the targets are children of the image they belong to.
//
// WHAT PRESSING ONE DOES, AND WHY IT IS NOT ALWAYS "RUN IT".
//
// A command shown with a choice or a placeholder cannot simply be run: someone
// has to say which. And a command that changes something should not fire
// because a sleeve brushed a screen that lives on a dashboard. Those open a
// terminal with the line typed and waiting; nothing happens until a person
// presses return.
//
// AND IT SAYS WHAT HAPPENED. Every button used to fail silently, so a refusal
// from logind, a masked target, a name that did not resolve and a tap that
// missed were one indistinguishable symptom: "the buttons dont work". The
// outcome now lands in the banner under them, in the words the command itself
// used.

import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
  id: root

  property var targets: []
  property var buttons: []
  property var actions: []
  property string armed: ""
  property var byCommand: ({})
  property real sheetW: 0
  property real sheetH: 0
  property string terminal: ""
  property string sheet: ""

  readonly property string home: Quickshell.env("HOME")

  // Where the commands are on the wallpaper.
  FileView {
    id: boxes
    path: root.home + "/.local/share/omacar/omacar-boxes.json"
    watchChanges: true
    onFileChanged: reload()
    onLoaded: {
      try {
        const d = JSON.parse(text());
        root.sheetW = d.width || 0;
        root.sheetH = d.height || 0;
        root.targets = d.boxes || [];
        root.buttons = d.buttons || [];
        // Cache-busted by the file's own name, which changes with its
        // contents, so a redrawn picture is a different source and the image
        // cannot show a stale copy.
        root.sheet = "file://" + root.home
                   + "/.local/share/omacar/omacar-commands.png?v=" + Date.now();
      } catch (e) {
        root.targets = [];
      }
    }
    onLoadFailed: root.targets = []
  }

  // What each of them needs in order to run. The same file the picture is
  // generated from, so a command cannot be tappable and unknown at once.
  FileView {
    id: data
    path: root.home + "/.local/share/omacar/omacar-commands.json"
    watchChanges: true
    onFileChanged: reload()
    onLoaded: {
      try {
        const d = JSON.parse(text());
        const map = {};
        for (const g of d.groups || []) {
          for (const c of g.commands || []) map[c.command] = c;
        }
        root.byCommand = map;
        root.actions = d.actions || [];
      } catch (e) {
        root.byCommand = ({});
      }
    }
  }

  Process {
    running: true
    command: ["sh", "-c",
      "for t in ghostty alacritty kitty foot wezterm konsole xterm; do " +
      "command -v $t >/dev/null && { echo $t; exit 0; }; done; echo ''"]
    stdout: StdioCollector { onStreamFinished: root.terminal = text.trim() }
  }

  Process { id: runner }

  // IT LOOKS AT WHAT HAPPENED. The old version set a command, set running, and
  // walked away. Four different things could stop `systemctl poweroff` — a
  // polkit challenge with nobody to answer it, a masked suspend.target that
  // `omacar tablet awake` masked on purpose, an inhibitor, a name that did not
  // resolve — and all four looked identical from the outside: a button that
  // did nothing. Now the failure is the sentence `omacar power` returns, on
  // the screen, next to the button that was pressed.
  Process {
    id: actionRunner
    stdout: StdioCollector { id: actionOut }
    stderr: StdioCollector { id: actionErr }
    onExited: (code, status) => {
      const said = (actionErr.text || actionOut.text || "").trim()
                     .split("\n")[0];
      if (code === 0) {
        root.say(said || (root.lastLabel + " — done"), false);
      } else {
        root.say(said || (root.lastLabel + " failed, and said nothing"), true);
      }
    }
  }

  // WHAT THE SCREEN IS CURRENTLY SAYING. A reference screen that swallows its
  // own errors is worse than one with no buttons, because it teaches you that
  // the tool is broken rather than that the machine refused.
  property string note: ""
  property bool noteBad: false
  property string lastLabel: ""

  function say(text, bad) {
    // Tone before text. Anything watching `note` reads `noteBad` in the same
    // turn, and setting them the other way round hands it the previous state's
    // colour for one pass.
    root.noteBad = bad;
    root.note = text;
    fade.restart();
  }

  Timer { id: fade; interval: 12000; onTriggered: root.note = "" }

  // ARM, THEN FIRE. Sleep and shutdown end whatever the machine was doing, and
  // this screen lives on a dashboard where a sleeve or a knee can find it. The
  // first press arms; the second, within a few seconds, does it. One extra tap
  // is a small price for making an accident impossible.
  //
  // FOUR SECONDS WAS TOO FEW AND THE SIGN WAS TOO SMALL. The armed state used
  // to be twelve-pixel text laid over a label that was already there, which at
  // arm's length in a car is a smudge; and the window closed before somebody
  // who had looked away could come back to it. So it says what the second
  // press will do, in the banner, and it waits ten seconds.
  Timer {
    id: disarm
    interval: 10000
    onTriggered: { root.armed = ""; if (root.note.indexOf("Press ") === 0) root.note = ""; }
  }

  function press(i) {
    const a = root.actions[i];
    if (!a || !a.run || !a.run.length) {
      // Never silent. If this fires it means the two files disagree about how
      // many buttons there are, which is a thing worth seeing rather than a
      // button that ignores you.
      root.say("this button has nothing behind it — run: omacar cheatsheet --set",
               true);
      return;
    }
    if (a.confirm && root.armed !== a.id) {
      root.armed = a.id;
      // `about` is the caption drawn under the label in the picture and is not
      // a sentence — "Press Shut down again to ends the day". Each action
      // carries the words for this moment instead.
      root.say(a.ask || ("Press " + a.label + " again"), false);
      disarm.restart();
      return;
    }
    root.armed = "";
    disarm.stop();
    root.lastLabel = a.label;
    root.say(a.label + "…", false);
    actionRunner.command = a.run;
    actionRunner.running = true;
  }

  function plain(cmd) {
    return String(cmd).replace(/\s*\[[^\]]*\]/g, "").trim();
  }

  function ambiguous(cmd) {
    const meta = root.byCommand[cmd];
    if (meta && meta.confirm) return true;
    return String(cmd).indexOf("[") >= 0 || plain(cmd).indexOf("|") >= 0;
  }

  function fire(cmd) {
    if (!root.terminal) return;
    const meta = root.byCommand[cmd] || {};
    const line = plain(cmd);
    const about = String(meta.description || "").replace(/"/g, "'");
    if (ambiguous(cmd)) {
      runner.command = [root.terminal, "-e", "bash", "-c",
        "printf '  %s\\n  %s\\n\\n' \"" + cmd + "\" \"" + about + "\"; " +
        "read -e -i \"" + line + "\" -p '> ' c; eval \"$c\"; " +
        "printf '\\n[done] '; read -n1"];
    } else {
      runner.command = [root.terminal, "-e", "bash", "-c",
        line + "; printf '\\n[done] '; read -n1"];
    }
    runner.running = true;
  }

  PanelWindow {
    id: board
    anchors { top: true; bottom: true; left: true; right: true }
    color: "#00000000"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Bottom
    // Never the keyboard. This sits under every window all the time, and a
    // surface behind your editor that ate your keystrokes would be the worst
    // possible behaviour for a reference.
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    // IT SHOWS THE PICTURE ITSELF, rather than lying over the wallpaper.
    //
    // Being transparent and trusting the wallpaper underneath was nearly
    // right and had one flaw with no fix: the wallpaper is drawn by another
    // surface, and two surfaces on the same layer have no defined order. Ours
    // could be under it, which puts every target beneath the thing it is
    // meant to be on top of.
    //
    // Drawing the same PNG removes the question. There is still exactly one
    // rendering — the picture — and now the targets are children of the image
    // they belong to, so they cannot be misaligned by anything.
    Image {
      id: sheet
      anchors.fill: parent
      source: root.sheet
      fillMode: Image.PreserveAspectFit
      cache: false
      asynchronous: true
      smooth: true
    }

    // The picture is drawn for one screen size, and PreserveAspectFit may
    // letterbox it on a screen of another shape. The targets are placed
    // against where the image actually landed, not against the window.
    readonly property real sx: root.sheetW > 0 ? sheet.paintedWidth / root.sheetW : 1
    readonly property real sy: root.sheetH > 0 ? sheet.paintedHeight / root.sheetH : 1
    readonly property real ox: (width - sheet.paintedWidth) / 2
    readonly property real oy: (height - sheet.paintedHeight) / 2

    // One target per header button, over where the picture drew it —
    // BIGGER THAN THE PILL IT SITS ON. The drawn buttons are sixty pixels tall
    // in a picture that is halved on the way to the screen, so the thing being
    // aimed at is under six millimetres of glass, in a moving car, with a
    // thumb. The picture cannot grow without crowding the heading; the target
    // can, and nobody sees it. Fourteen pixels of margin roughly doubles it.
    Repeater {
      model: root.buttons
      Item {
        id: btn
        required property var modelData
        readonly property var act: root.actions[modelData.index] || ({})
        readonly property bool isArmed: act.id !== undefined && root.armed === act.id
        readonly property real pad: 14 * board.sx
        x: board.ox + modelData.x * board.sx - pad
        y: board.oy + modelData.y * board.sy - pad
        width: modelData.w * board.sx + pad * 2
        height: modelData.h * board.sy + pad * 2

        HoverHandler { id: bhover }
        TapHandler { onTapped: root.press(modelData.index) }

        // The highlight stays the size of the drawn pill, so what lights up is
        // what you can see. Only the reach is larger.
        Rectangle {
          id: glow
          anchors.fill: parent
          anchors.margins: btn.pad
          radius: height / 2
          color: btn.isArmed ? "#88d04b6b"
               : (bhover.hovered ? "#267fd0ff" : "#00000000")
          border.color: btn.isArmed ? "#ffd04b6b"
                      : (bhover.hovered ? "#557fd0ff" : "#00000000")
          border.width: btn.isArmed ? 2 : 1
          Behavior on color { ColorAnimation { duration: 90 } }

          // Armed pulses, because a label that cannot change is underneath it
          // and a still red box reads as decoration.
          SequentialAnimation {
            running: btn.isArmed
            loops: Animation.Infinite
            onRunningChanged: if (!running) glow.opacity = 1
            NumberAnimation { target: glow; property: "opacity"; to: 0.4; duration: 420 }
            NumberAnimation { target: glow; property: "opacity"; to: 1.0; duration: 420 }
          }
        }
      }
    }

    // WHAT JUST HAPPENED, WHERE THE BUTTONS ARE. Sized off the picture rather
    // than the window so it matches the type around it on any screen.
    Rectangle {
      visible: root.note !== ""
      anchors.horizontalCenter: parent.horizontalCenter
      y: board.oy + 170 * board.sy
      width: Math.min(label.implicitWidth + 40 * board.sx, board.width * 0.9)
      height: label.implicitHeight + 22 * board.sy
      radius: height / 4
      color: root.noteBad ? "#f22a1220" : "#f2121a12"
      border.width: 1
      border.color: root.noteBad ? "#ffd04b6b" : "#556f9c6f"
      Text {
        id: label
        anchors.centerIn: parent
        width: board.width * 0.9 - 40 * board.sx
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        text: root.note
        color: root.noteBad ? "#ffd9c4c4" : "#ffd6e4d6"
        font.pixelSize: Math.max(12, 30 * board.sy)
      }
    }

    Repeater {
      model: root.targets
      Rectangle {
        required property var modelData
        x: board.ox + modelData.x * board.sx
        y: board.oy + modelData.y * board.sy
        width: modelData.w * board.sx
        height: modelData.h * board.sy
        radius: 6 * board.sx
        // Only visible under a finger. A reference that glowed all over would
        // be competing with the thing it is meant to be showing.
        color: hover.hovered ? "#1e7fd0ff" : "#00000000"
        border.color: hover.hovered ? "#557fd0ff" : "#00000000"
        border.width: 1
        Behavior on color { ColorAnimation { duration: 90 } }

        HoverHandler { id: hover }
        TapHandler { onTapped: root.fire(modelData.command) }
      }
    }
  }
}
