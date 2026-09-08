# OmaCar Python environment.
#
# python-obd is not in the Arch repos, and ELM327-emulator's build script
# still imports pkg_resources — removed in setuptools 81. So: a venv, with
# setuptools pinned below 81 and build isolation off, which is the only
# combination that installs the emulator on Python 3.14.

OMACAR_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/omacar"
OMACAR_STATE="${XDG_STATE_HOME:-$HOME/.local/state}/omacar"
OMACAR_VENV="$OMACAR_DATA/venv"
OMACAR_PY="$OMACAR_VENV/bin/python"
OMACAR_ELM="$OMACAR_VENV/bin/elm"

mkdir -p "$OMACAR_STATE"

omacar_have_env() { [[ -x "$OMACAR_PY" ]] && "$OMACAR_PY" -c 'import obd' 2>/dev/null; }

omacar_need_env() {
  omacar_have_env && return 0
  echo "omacar: Python environment missing — run: omacar setup" >&2
  exit 1
}

omacar_setup_env() {
  mkdir -p "$OMACAR_DATA"
  echo "  building the OmaCar Python environment…"
  python3 -m venv "$OMACAR_VENV"
  "$OMACAR_VENV/bin/pip" install -q --upgrade pip
  # pkg_resources went away in setuptools 81; the emulator's setup.py needs it.
  "$OMACAR_VENV/bin/pip" install -q "setuptools<81" wheel
  "$OMACAR_VENV/bin/pip" install -q obd
  # pyusb, for the CarPlay dongle. It is a thin ctypes wrapper over the system
  # libusb, so it needs `libusb` present -- which Arch has by default. Failing
  # to install it must not stop the rest: the car half of this tool has no use
  # for USB, and somebody with no dongle should not be blocked by it.
  "$OMACAR_VENV/bin/pip" install -q pyusb ||
    echo "  ! pyusb did not install; the phone screen will not find a dongle" >&2
  "$OMACAR_VENV/bin/pip" install -q --no-build-isolation ELM327-emulator ||
    echo "  ! the bench emulator failed to install; the rest still works" >&2
  echo "  ready: $("$OMACAR_PY" -c 'import obd; print("python-obd", obd.__version__)')"
}
