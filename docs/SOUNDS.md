# Changing the sound

The built-in synth is sine harmonics with an envelope. It keeps time and it
tells you which note is which, and it does not sound like a piano. For anything
you want to listen to while you practise, use FluidSynth.

Swapping instruments is really swapping SoundFonts, and nothing in the config
says so. This is that missing page.

## What a SoundFont is

A `.sf2` file holds recorded samples of an instrument, laid out so a synthesiser
can pitch and blend them into any note. FluidSynth reads it; `audio.program`
picks which sound inside it to use.

Two consequences worth knowing before you go looking for one.

**Bigger usually is better.** The size is mostly recorded audio. A 4 MB font has
a handful of samples stretched across the keyboard; a 200 MB one has separate
recordings per register and per velocity layer, which is what makes soft playing
sound soft rather than quiet.

**`program` indexes into whatever the font provides, not into General MIDI.** GM
is a convention: program 0 is *meant* to be an acoustic grand. A font is free to
put anything anywhere. So ask the file rather than guessing:

```bash
psv -c piano.toml instruments
```

With no SoundFont configured that lists the 128 General MIDI names and flags the
ones worth trying on a piano piece. With one configured it reads the font's own
preset names, which is what will actually sound. Add `-v` to see the variation
banks as well as bank 0.

## Getting one

| SoundFont | Size | Licence | Notes |
| --- | --- | --- | --- |
| GeneralUser GS | ~30 MB | Free, permissive | A full GM set. The sensible default, and what `piano.toml` points at |
| Salamander Grand | ~1 GB | CC BY | Sixteen velocity layers of a Yamaha C5. The one to get if you only care about piano |
| FluidR3 GM | ~140 MB | MIT-like | The other common full GM set, bundled with many Linux distributions |

Search the name; each has an obvious home page. Download the `.sf2` (or `.sf3`,
which is the same thing compressed, and FluidSynth reads it) and put it
somewhere stable.

## Pointing psv at it

```toml
[audio]
backend = "fluidsynth"
soundfont = "~/.local/fluidsynth/GeneralUser-GS.sf2"
fluidsynth_bin = "~/.local/fluidsynth/bin"   # Windows only, see below
program = 0
stereo_width = 0.5
```

`~` expands, so a config file with a home-relative path works on any machine.

`fluidsynth_bin` is the folder holding the native FluidSynth library. It is
named here rather than expected on `PATH` because `pyfluidsynth` finds the DLL
through `ctypes.util.find_library`, which on Windows searches `PATH` and nothing
else. Editing your environment permanently to satisfy one optional backend is a
poor trade, so psv prepends this folder for the length of the run instead. On
Linux and macOS the system package manager puts the library where it will be
found and you can leave this empty.

If anything is missing, the render does not fail. It falls back to the built-in
synth and says which piece was missing, because a silent video with no
explanation is much worse than a cheap-sounding one that tells you why.

## Trying a different instrument

```bash
psv -c piano.toml instruments          # what is in the font
psv -c piano.toml run song.mid -o out.mp4 --bars 1-8 --width 640 --height 360
```

Eight bars at a small size costs a second or two, which is the right way to
audition a sound. Change `audio.program` and run it again.

On a GM-compatible font, the ones worth trying on piano writing:

| Program | Sound |
| --- | --- |
| 0 | Acoustic grand. The default, and what most fonts sample best |
| 1 | Bright grand. Cuts through a busy texture |
| 4 | Rhodes electric piano |
| 5 | FM electric piano |
| 6 | Harpsichord. No dynamics at all, so velocity stops meaning anything |
| 11 | Vibraphone |
| 19 | Church organ, for the organ repertoire this tool arranges from |

## Stereo

`audio.stereo_width` spreads the built-in synth by register: low notes to the
left, high notes to the right, as they sit under your hands. It is not only
prettier. The two hands stop competing for the same place in the mix, which is
what makes a left-hand line audible underneath a busy right hand.

0 is mono. 1 sends the extremes hard left and right, wider than any real piano.
0.5 is the default and sounds like sitting at one.

FluidSynth produces its own stereo image from the SoundFont, so this setting
does not apply to it.

## Using a recording instead

If you already have audio of the piece, skip synthesis entirely:

```toml
[audio]
backend = "mux"
audio_file = "~/music/the-recording.flac"
offset_s = 0.12    # nudge it into sync; positive delays the audio
```

The video is rendered from the MIDI either way, so the two only line up if the
recording follows the same tempo. It usually will not exactly, which is what
`offset_s` is for and also why this is worth trying only on a piece where the
MIDI came from that recording.

One limitation: the count-in and metronome cannot be mixed into a file you
already have, so `--count-in` and `--metronome` are silent under this backend.
psv says so rather than dropping them quietly.
