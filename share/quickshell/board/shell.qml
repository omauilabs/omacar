// The command board: the wallpaper reference, but you can press it.
//
// WHY THIS IS NOT THE WALLPAPER.
//
// A wallpaper is an image and an image cannot be pressed. What sits between a
// wallpaper and your windows on Wayland is a layer-shell surface on the bottom
// layer, which is a real surface that takes input -- so this is one of those,
// drawing the same commands the picture draws and running them when tapped.
//
// It reads the list from the file `omacar cheatsheet` writes. Not by parsing
// the CLI a second time: two parsers is two chances to disagree about what the
// commands are, and the whole point of both surfaces is that they cannot.
//
// WHAT HAPPENS WHEN YOU PRESS ONE, AND WHY IT IS NOT ALWAYS "RUN IT".
//
// A command shown with a choice or a placeholder in it -- `write arm|disarm`,
// `live [PID...]` -- cannot simply be run, because somebody has to say which.
// And a command that changes something should not fire because a sleeve
// brushed a screen that lives on a dashboard.
//
// So: unambiguous, harmless commands run. Everything else opens a terminal
// with the line typed and waiting, and nothing happens until a person presses
// return. That is one keypress away from "it fires", and a long way from a
// diagnostic tool that started a sweep because the tablet was leant on.

import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import Quickshell.Wayland

ShellRoot {
  id: root

  property var groups: []
  property string statusLine: "reading the command list…"
  property string dataPath: Quickshell.env("HOME") + "/.local/share/omacar/omacar-commands.json"

  // The terminals worth trying, in the order somebody is likely to have them.
  readonly property var terminals: [
    "ghostty", "alacritty", "kitty", "foot", "wezterm", "konsole", "xterm"
  ]
  property string terminal: ""

  FileView {
    id: data
    path: root.dataPath
    watchChanges: true
    onFileChanged: reload()
    onLoaded: {
      try {
        const doc = JSON.parse(text());
        root.groups = doc.groups || [];
        let n = 0;
        for (const g of root.groups) n += (g.commands || []).length;
        root.statusLine = n + " commands";
      } catch (e) {
        root.statusLine = "the command list would not parse: " + e;
      }
    }
    onLoadFailed: {
      root.statusLine = "no command list yet — run: omacar cheatsheet";
    }
  }

  // Which terminal exists here. Asked once, rather than guessed.
  Process {
    id: findTerminal
    running: true
    command: ["sh", "-c",
      "for t in ghostty alacritty kitty foot wezterm konsole xterm; do " +
      "command -v $t >/dev/null && { echo $t; exit 0; }; done; echo ''"]
    stdout: StdioCollector {
      onStreamFinished: root.terminal = text.trim()
    }
  }

  Process { id: runner }

  // Strip the optional parts a usage line carries, and say whether what is
  // left is something that can just be run.
  function plain(cmd) {
    return cmd.replace(/\s*\[[^\]]*\]/g, "").trim();
  }
  function ambiguous(entry) {
    return entry.confirm || plain(entry.command).indexOf("|") >= 0
        || entry.command.indexOf("[") >= 0;
  }

  function fire(entry) {
    const line = plain(entry.command);
    if (!root.terminal) {
      root.statusLine = "no terminal found to run it in";
      return;
    }
    if (ambiguous(entry)) {
      // Typed and waiting. `read -e -i` puts an editable line in front of the
      // person, so a choice can be finished and nothing runs until return.
      runner.command = [root.terminal, "-e", "bash", "-c",
        "printf '  %s\\n  %s\\n\\n' \"" + entry.command + "\" \"" +
        entry.description.replace(/"/g, "'") + "\"; " +
        "read -e -i \"" + line + "\" -p '> ' c; eval \"$c\"; " +
        "printf '\\n[done] '; read -n1"];
      root.statusLine = "ready: " + line;
    } else {
      runner.command = [root.terminal, "-e", "bash", "-c",
        line + "; printf '\\n[done] '; read -n1"];
      root.statusLine = "ran: " + line;
    }
    runner.running = true;
  }

  PanelWindow {
    id: board
    anchors { top: true; bottom: true; left: true; right: true }
    color: "#00000000"
    exclusionMode: ExclusionMode.Ignore
    WlrLayershell.layer: WlrLayer.Bottom
    // NOT KEYBOARD FOCUS. This sits under every window, all the time. Taking
    // the keyboard would mean the thing behind your editor was eating your
    // keystrokes, which is the worst possible behaviour for a reference.
    WlrLayershell.keyboardFocus: WlrKeyboardFocus.None

    Rectangle {
      anchors.fill: parent
      gradient: Gradient {
        GradientStop { position: 0.0; color: "#111a24" }
        GradientStop { position: 0.55; color: "#0d1319" }
        GradientStop { position: 1.0; color: "#090d12" }
      }
    }

    ColumnLayout {
      anchors.fill: parent
      anchors.margins: Math.round(parent.height * 0.045)
      spacing: Math.round(parent.height * 0.018)

      RowLayout {
        spacing: 12
        Text {
          text: "OmaCar"
          color: "#e8eef5"
          font.pixelSize: Math.round(board.height * 0.031)
          font.weight: Font.DemiBold
        }
        Text {
          text: "press a command to run it"
          color: "#7d90a4"
          font.pixelSize: Math.round(board.height * 0.0155)
          Layout.alignment: Qt.AlignBottom
          bottomPadding: Math.round(board.height * 0.004)
        }
        Item { Layout.fillWidth: true }
        Text {
          text: root.statusLine
          color: "#5d92b4"
          font.pixelSize: Math.round(board.height * 0.0145)
          Layout.alignment: Qt.AlignBottom
          bottomPadding: Math.round(board.height * 0.004)
        }
      }

      Rectangle { Layout.fillWidth: true; height: 1; color: "#2b3a4a" }

      GridLayout {
        Layout.fillWidth: true
        Layout.fillHeight: true
        columns: 4
        columnSpacing: Math.round(board.width * 0.024)
        rowSpacing: 0

        Repeater {
          model: root.groups
          ColumnLayout {
            required property var modelData
            Layout.alignment: Qt.AlignTop
            Layout.fillWidth: true
            Layout.preferredWidth: 1
            spacing: 2

            Text {
              text: modelData.title
              color: "#d7e3f0"
              font.pixelSize: Math.round(board.height * 0.0162)
              font.weight: Font.DemiBold
              topPadding: Math.round(board.height * 0.014)
            }
            Text {
              text: modelData.about
              color: "#6f8296"
              font.pixelSize: Math.round(board.height * 0.0132)
              wrapMode: Text.WordWrap
              Layout.fillWidth: true
              bottomPadding: Math.round(board.height * 0.006)
            }

            Repeater {
              model: modelData.commands
              Rectangle {
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: entry.implicitHeight + 10
                radius: 5
                color: hover.hovered ? "#182430" : "#00000000"
                border.color: hover.hovered ? "#2b3a4a" : "#00000000"
                border.width: 1

                HoverHandler { id: hover }
                TapHandler {
                  onTapped: root.fire(modelData)
                }

                ColumnLayout {
                  id: entry
                  anchors.left: parent.left
                  anchors.right: parent.right
                  anchors.verticalCenter: parent.verticalCenter
                  anchors.leftMargin: 5
                  anchors.rightMargin: 5
                  spacing: 0
                  Text {
                    text: modelData.command
                    color: root.ambiguous(modelData) ? "#9ec4dd" : "#7fd0ff"
                    font.family: "monospace"
                    font.pixelSize: Math.round(board.height * 0.0142)
                    Layout.fillWidth: true
                    elide: Text.ElideRight
                  }
                  Text {
                    text: modelData.description
                    color: "#94a7ba"
                    font.pixelSize: Math.round(board.height * 0.0130)
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                  }
                }
              }
            }
          }
        }
      }

      Text {
        text: "a dimmer command opens a terminal with the line ready — "
            + "nothing runs until you press return"
        color: "#56697c"
        font.pixelSize: Math.round(board.height * 0.0122)
      }
    }
  }
}
