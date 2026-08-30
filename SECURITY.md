# Security Policy

## Supported versions

This project is pre-alpha. Only the `main` branch is supported.

## Reporting a vulnerability

Report privately through GitHub's
[private vulnerability reporting](https://github.com/RinkyDinkyNooble/piano-song-to-visual/security/advisories/new)
rather than opening a public issue. Expect an initial response within a week.

Please include a description of the issue, the input that triggers it, and the impact
you believe it has.

## Threat model

This is a local command-line tool with no network listener and no privileged operations.
The realistic attack surface is **untrusted input files**:

- Malformed or hostile MIDI files reaching the MIDI parser. This is the primary
  surface: MIDI is the tool's only input format.
- Malformed SoundFont (`.sf2`) files reaching FluidSynth, and user-supplied audio
  files reaching ffmpeg when the mux backend is used.
- Paths, filenames, and config values flowing into the ffmpeg invocation.
- Config files, which are parsed as TOML and validated before use.

Things that are in scope: parser crashes that are exploitable, resource exhaustion from
crafted input, command or argument injection through filenames or config values, path
traversal in output handling, and unsafe deserialisation.

Things that are not in scope: an ugly arrangement, an unmusical reduction, or the
tool being slow on a large file.

## What the project does about it

- `ruff`'s bandit ruleset and CodeQL run on every push and pull request.
- Dependencies are updated weekly by Dependabot.
- No input-derived string is ever passed to a shell; subprocesses are invoked with
  argument lists.
