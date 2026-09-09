// The command board: a sheet of invisible targets over the wallpaper.
//
// WHY IT DRAWS NOTHING.
//
// It used to draw the whole reference itself — its own background, its own
// columns, its own type. That is a second rendering of a thing that already
// exists as a picture, and two renderings of one thing are two things to keep
// in step. They did not stay in step, and because this one sits on top it won:
// a corrected wallpaper appeared for a moment and was then painted over by an
// older-looking copy of itself.
//
// So the wallpaper is the only rendering. This is a transparent layer above it
// carrying one rectangle per command, in the exact places the picture put them
// — `omacar cheatsheet` writes those coordinates out when it draws. Hovering
// one lifts it slightly; pressing one runs it. Nothing here can disagree with
// what is on the screen, because it does not draw what is on the screen.
//
// WHAT PRESSING ONE DOES, AND WHY IT IS NOT ALWAYS "RUN IT".
//
// A command shown with a choice or a placeholder cannot simply be run: someone
// has to say which. And a command that changes something should not fire
// because a sleeve brushed a screen that lives on a dashboard. Those open a
// terminal with the line typed and waiting; nothing happens until a person
// presses return.

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
  Process { id: actionRunner }

  // ARM, THEN FIRE. Sleep and shutdown end whatever the machine was doing, and
  // this screen lives on a dashboard where a sleeve or a knee can find it. The
  // first press arms; the second, within a few seconds, does it. One extra tap
  // is a small price for making an accident impossible.
  Timer {
    id: disarm
    interval: 4000
    onTriggered: root.armed = ""
  }

  function press(i) {
    const a = root.actions[i];
    if (!a || !a.run || !a.run.length) return;
    if (a.confirm && root.armed !== a.id) {
      root.armed = a.id;
      disarm.restart();
      return;
    }
    root.armed = "";
    disarm.stop();
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

    // One target per header button, over where the picture drew it.
    Repeater {
      model: root.buttons
      Rectangle {
        required property var modelData
        readonly property var act: root.actions[modelData.index] || ({})
        readonly property bool isArmed: act.id !== undefined && root.armed === act.id
        x: board.ox + modelData.x * board.sx
        y: board.oy + modelData.y * board.sy
        width: modelData.w * board.sx
        height: modelData.h * board.sy
        radius: height / 2
        color: isArmed ? "#66d04b6b"
             : (bhover.hovered ? "#267fd0ff" : "#00000000")
        border.color: isArmed ? "#ffd04b6b"
                    : (bhover.hovered ? "#557fd0ff" : "#00000000")
        border.width: 1
        Behavior on color { ColorAnimation { duration: 90 } }
        HoverHandler { id: bhover }
        TapHandler { onTapped: root.press(modelData.index) }

        // Armed says so, because the label underneath cannot change.
        Text {
          anchors.centerIn: parent
          visible: parent.isArmed
          text: "press again"
          color: "#ffe4ec"
          font.pixelSize: Math.max(10, parent.height * 0.42)
        }
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
