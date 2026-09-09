// Dismissing the boot screen, and saying what it is waiting for.
//
// THE SPLASH ITSELF IS MARKUP AND CSS, deliberately: it has to be on the
// screen on the first frame, and a module that draws it cannot be, because by
// then the frame it was meant to cover has already been painted empty. So this
// file never creates it. It only decides when it goes, and what it says while
// it is there.
//
// IT DOES NOT TOUCH main.js. The app already announced its own readiness by
// flipping `data-booting` on #app — that is what un-hides the stage — so this
// watches that attribute rather than being called by it. Two consequences
// worth having: the boot screen cannot delay the app by being wrong, and the
// app's own failure path (which sets the same attribute) dismisses it for
// free, so a server that is not running gets the error card and not a mark
// spinning over the top of it.

const boot = document.getElementById("boot");
const app = document.getElementById("app");

// A FLOOR AND A CEILING, and the floor is the smaller point.
//
// Ceiling first: past this the screen belongs to the app whatever state it is
// in, because a brand mark held over a dead daemon is the tool lying about
// itself. The floor is not vanity — on a fast machine the app is ready in
// under a tenth of a second, and a mark that appears and vanishes inside one
// is a flash of light in a dark car, at night, at eye level.
const FLOOR = 780;
const CEILING = 6000;

// What to say, and when it stops being decoration. Nothing here claims
// progress: this is a list of how long the wait has been, in words.
const SAID = [
  [0, "starting"],
  [2200, "waiting for the server"],
  [4200, "the server is not answering — omacar server status"],
];

if (boot && app) {
  const began = performance.now();
  const said = boot.querySelector(".boot-said");
  let done = false;

  const tell = (text, slow) => {
    if (said && said.textContent !== text) said.textContent = text;
    boot.dataset.slow = slow ? "1" : "0";
  };
  tell(SAID[0][1], false);

  const dismiss = () => {
    if (done) return;
    done = true;
    clearInterval(ticker);
    obs.disconnect();
    boot.dataset.done = "1";
    // Out of the accessibility tree as well as out of sight. It is removed
    // rather than left hidden so nothing can focus into it later.
    boot.setAttribute("aria-hidden", "true");
    setTimeout(() => boot.remove(), 700);
  };

  // The floor applies to a ready app, never to the ceiling: a boot that has
  // taken six seconds does not then get held for another half of one.
  const ready = () => {
    const left = FLOOR - (performance.now() - began);
    if (left > 0) setTimeout(dismiss, left);
    else dismiss();
  };

  const ticker = setInterval(() => {
    const waited = performance.now() - began;
    if (waited >= CEILING) { dismiss(); return; }
    for (const [after, text] of SAID) {
      if (waited >= after) tell(text, after > 0);
    }
  }, 200);

  const obs = new MutationObserver(() => {
    if (app.dataset.booting === "0") ready();
  });
  obs.observe(app, { attributes: true, attributeFilter: ["data-booting"] });
  // It may already have finished before this module was even fetched.
  if (app.dataset.booting === "0") ready();

  // THE FILM IS OPTIONAL AND IS NOT IN THIS REPOSITORY. Asked for by HEAD
  // first, so the ordinary case — no film — costs one loopback round trip and
  // leaves no failed media element behind. Drop one at
  // ~/.local/share/omacar/boot.webm and it plays instead of the mark.
  const film = boot.querySelector(".boot-film");
  if (film) {
    fetch("/boot-film", { method: "HEAD" })
      .then((r) => {
        if (!r.ok || done) return;
        film.src = "/boot-film";
        // Only shown once it is actually playing. A film that stalls or
        // cannot be decoded leaves the mark where it was.
        film.addEventListener("playing", () => {
          if (!done) boot.dataset.film = "1";
        }, { once: true });
        // And it never extends the wait: when it ends, the screen goes,
        // whether or not it has been watched to the end.
        film.addEventListener("ended", dismiss, { once: true });
        film.play().catch(() => {});
      })
      .catch(() => {});
  }

  // SKIPPABLE, BUT NEVER INTO AN EMPTY SCREEN. A tap ends the film and the
  // floor; it does not reveal an app that has nothing on it yet, because the
  // stage stays hidden until `data-booting` is "0" and this respects that.
  const skip = () => {
    if (film) { try { film.pause(); } catch { /* nothing to pause */ } }
    boot.dataset.film = "0";
    if (app.dataset.booting === "0") dismiss();
  };
  boot.addEventListener("pointerdown", skip);
  window.addEventListener("keydown", skip, { once: true });
}
