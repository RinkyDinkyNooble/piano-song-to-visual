# Security Policy

## Supported versions

Only the latest release and the `main` branch are supported. There is no
backport policy: a fix lands on `main` and goes out in the next release.

## Reporting a vulnerability

Report privately through GitHub's
[private vulnerability reporting](https://github.com/RinkyDinkyNooble/piano-song-to-visual/security/advisories/new)
rather than opening a public issue. Expect an initial response within a week.

Please include a description of the issue, the input that triggers it, and the impact
you believe it has.

## Threat model

This is a local command-line tool with no network listener and no privileged operations.
The realistic attack surface is **untrusted input files**:

- Malformed or hostile MIDI files reaching the MIDI parser.
- Malformed MusicXML reaching the score reader, in both its plain and its
  compressed (`.mxl`) form. A `.mxl` is a zip, so it carries the usual archive
  risks: the member to read is taken from the container manifest by name rather
  than guessed, a member path that escapes the archive is refused, and a member
  that declares an implausible uncompressed size is refused before it is read.
  The XML itself goes through `xml.etree`, which does not resolve external
  entities: an XXE attempt fails as an undefined entity rather than reading the
  file it names. Internal entities *are* expanded, and the guard against a
  billion-laughs expansion is expat's input amplification limit rather than
  anything this project does. That limit arrived in expat 2.6, so it is present
  in current CPython but not in the oldest 3.12 patch releases.
- Malformed SoundFont (`.sf2`) files reaching FluidSynth, and user-supplied audio
  files reaching ffmpeg when the mux backend is used. The `.sf2` chunk walker
  checks every offset against the real file length before reading.
- Paths, filenames, and config values flowing into the ffmpeg invocation.
- Config files, which are parsed as TOML and validated before use.

Things that are in scope: parser crashes that are exploitable, resource exhaustion from
crafted input, command or argument injection through filenames or config values, path
traversal in output handling, and unsafe deserialisation.

Things that are not in scope: an ugly arrangement, an unmusical reduction, or the
tool being slow on a large file.

One known limit, stated rather than left to be found: an uncompressed MusicXML
file is parsed without a size cap, so a multi-gigabyte one will exhaust memory.
The compressed form is capped because a zip can hide its true size behind a
small file; a plain file cannot, and its size is visible before you open it.

## What the project does about it

- `ruff`'s bandit ruleset and CodeQL's `security-and-quality` queries run on
  every push and pull request.
- Dependencies are updated weekly by Dependabot.
- No input-derived string is ever passed to a shell; subprocesses are invoked with
  argument lists.
