# Contributing

This is a personal tool that happens to be public. It is finished in the sense
that it does what it was built to do, so the honest expectation to set is that
bug reports are more welcome than large features, and that a feature nobody
asked for is likely to be declined however well it is written.

None of that is a reason not to open an issue. Ask first if the change is more
than a fix.

## Getting set up

```bash
git clone https://github.com/RinkyDinkyNooble/piano-song-to-visual
cd piano-song-to-visual
pip install -e ".[all,dev]"
pre-commit install
```

Python 3.12 or newer. No system ffmpeg is needed; the `render` extra brings its
own binary.

## The four checks

CI runs exactly these, and nothing else. Run them before opening a pull request
and there will be no surprises:

```bash
ruff check .
ruff format --check .
mypy
pytest
```

`mypy` with no arguments is deliberate. It covers the tests as well as the
package, and `mypy --strict src/` is the weaker check despite looking stricter.

The suite runs green on a fresh clone with no network. Two optional fetch
scripts add more material, and the tests that need it skip when it is absent:

```bash
python scripts/fetch_test_songs.py    # two CC BY-SA songs
python scripts/fetch_test_scores.py   # 29 MusicXML conformance files
```

## What the project will not trade away

Three rules that override any other argument, including a benchmark.

**The hand-span limit is an invariant, not a heuristic.** Nothing may make it
conditional on difficulty, genre, or musical judgement. `verify_span` runs at
the end of every `constrain`, not only in tests, and a non-empty result is a
hard failure. Any change touching the constraint engine needs a test asserting
the invariant on the *output*, not a test that some function was called.

**Readability beats spectacle in the renderer.** The video exists to be learned
from. Visual effects are welcome as opt-in configuration and unwelcome as
defaults. Where a visual choice trades clarity for looks, clarity wins.

**Music never goes missing quietly.** A stage may remove notes. It may not do so
without recording which and why. Every note the constraint engine touches
carries its provenance, and that is what makes the engine auditable rather than
a black box.

## How the code is arranged

Four stages, each a function over a serialisable `Score`:

```
Path -> parse -> Score -> arrange -> Score -> constrain -> Score -> render -> frames -> video
```

Stages stay decoupled. No stage imports the CLI, and no stage reaches into
another's internals; if stage N+1 needs stage N's internal state, the `Score`
model is missing a field. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) has the
detail.

`render_frame(score, config, t)` is a pure function of the score and a time.
That is not only for testing: it is what lets the timeline be cut into spans and
rendered across processes, and what the committed reference images rest on. Keep
it pure. Nothing in the renderer may read the previous frame.

Before writing a loop over note events or over video frames, look for the one
that already exists. Five modules once hand-wrote the same press/release sweep
and all five carried the same bug; there is now one, in `psv/sweep.py`. Video
decoding in tests belongs to `tests/probe.py` for the same reason.

## Tests

Every feature the tool promises is registered in `tests/features.toml` and
cannot be marked done until a test claims it, via `@pytest.mark.feature`.

Three kinds of test carry most of the weight:

- **Property tests** for guarantees. The span invariant is generated over
  thousands of random scores rather than asserted on three examples.
- **Reference images** for the renderer, at a small resolution, committed and
  compared pixel for pixel. Regenerate deliberately with
  `python scripts/make_references.py` and say in the commit message why the
  picture changed.
- **A test that fails on the old code.** A regression test that passes before
  the fix is not a regression test. A reference image regenerated with a fault
  in it will hide that fault for as long as it lives, which has happened here.

## Fixing a bug

Build the failing check before forming a theory about the cause. If you catch
yourself reasoning about a bug you cannot reproduce on demand, stop and go build
the reproduction; reading the code will not save you, and this project has the
scars to prove it. A vertical-line artefact in the renderer survived three
attempts at fixing it by inspection and fell over in minutes once there was a
detector that measured it.

Prefer a two-second deterministic loop to a thirty-second flaky one: shrink the
resolution, cut the piece to four bars, seed the randomness. Render 3 seconds at
320x180 and 10 fps rather than debugging a 1080p60 render.

Then shrink the input until removing anything makes it pass. That minimal case
is also the regression test.

## Writing

Docs, docstrings, comments and commit messages are read by people. Prefer simple
verbs and concrete detail. Say what a thing does and why it is that way, not
that it is powerful, seamless, or comprehensive. Comments describe how the code
behaves now; what it used to do belongs in `CHANGELOG.md` and the commit
message.

Do not state a measurement you have not taken. "About 14,400 frames" is
arithmetic and fine. "Three times faster" needs a number behind it.

## Licence

By contributing you agree that your contribution is licensed under
[0BSD](LICENSE), the same terms as the rest of the project: no conditions on
anyone downstream, including no attribution requirement.
