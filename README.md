# ClipRadar

ClipRadar is a local-first Windows desktop workflow for turning new YouTube uploads into a small number of ranked, edited short-form clips:

`Channel → upload detection → local candidates → Gemini ranking → 9:16 render → review`

The first usable version deliberately stops at the review queue. Publishing is isolated as the next module instead of weakening the core workflow.

## What works

- Add channels using a URL, `@handle`, channel ID, or a video URL.
- Persist channels, source videos, scheduled jobs, candidates, usage, and rendered clips in SQLite.
- Establish a safe baseline when a channel is first added so old uploads are not analyzed without limit.
- Check a bounded recent-upload window and apply a per-channel analysis delay.
- Manually analyze the latest upload or a specific video.
- Download source media first, then fetch at most one available English VTT caption track as an optional best-effort enhancement.
- Preselect candidate ranges locally using audio energy, transcript reactions, scene changes, and YouTube heatmap data when available.
- Send only compressed candidate previews to the selected Gemini model.
- Apply hard daily limits for videos, source minutes, clips, and estimated AI cost.
- De-duplicate overlapping moments and render only the best candidates.
- Render 9:16 H.264 MP4 with face-biased reframing, burned captions, and loudness normalization.
- Preview, approve, reject, regenerate, open, or trace a clip back to its source in the review queue.
- Recover interrupted jobs safely after an application restart.
- Build and smoke-test a bundled Windows EXE through GitHub Actions.

## Security

The Gemini API key is entered only through **Settings → AI**. It is stored through `keyring`; on Windows this uses the operating-system credential vault. The key is never stored in SQLite, project files, logs, Git, or the executable. Model and non-secret AI settings are stored in SQLite.

## Run from source on Windows

Requirements: Python 3.12+, FFmpeg/FFprobe on `PATH`, and a current Windows runtime.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
$env:PYTHONPATH = "src"
python -m clipradar
```

Enter the Gemini key and choose a model in **Settings → AI**, use **Test connection**, then **Save**. No configuration file needs editing.

## Build the Windows package

```powershell
.\build_windows.ps1
```

The result is `dist\ClipRadar-Windows-x64.zip`. Extract it and start `ClipRadar.exe`. FFmpeg and FFprobe are included inside the application folder.

Every push to `main` also runs tests, builds the package on `windows-latest`, starts the packaged EXE in self-test mode, and publishes a downloadable workflow artifact.

## Test

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
python -m clipradar --self-test
```

Tests cover persistence, secure-secret separation, channel baseline and upload scheduling, local candidate selection, real FFmpeg rendering, a deterministic full pipeline through the review queue, UI navigation, and the packaged executable.

## Runtime data

ClipRadar keeps its database, downloads, candidate previews, logs, and output in the platform application-data directory. The output folder can be changed in **Settings → Storage**. Candidate previews are deleted after analysis by default. Source downloads are kept by default so regeneration remains immediate.

## Architecture

The code is split by responsibility under `src/clipradar/`: `channels`, `monitoring`, `jobs`, `youtube`, `media`, `analysis`, `ai`, `rendering`, `review`, `settings`, `storage`, and `ui`. Network, Gemini, download, detection, and rendering operations run in a background executor; Qt's UI thread only coordinates and displays state.
