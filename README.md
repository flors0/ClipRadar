# ClipRadar

ClipRadar is a local-first Windows desktop workflow for turning new YouTube uploads into a small number of ranked, edited short-form clips:

`Channel → upload detection → local candidates → Gemini ranking + metadata + framing → 9:16 render → review → YouTube`

Publishing remains approval-driven: nothing is uploaded until a user confirms the final clip and its metadata.

## What works

- Add channels using a URL, `@handle`, channel ID, or a video URL.
- Persist channels, source videos, scheduled jobs, candidates, usage, and rendered clips in SQLite.
- Establish a safe baseline when a channel is first added so old uploads are not analyzed without limit.
- Check a bounded recent-upload window and apply a per-channel analysis delay.
- Manually analyze the latest upload or a specific video.
- Download source media first, then fetch at most one available English VTT caption track as an optional best-effort enhancement.
- Preselect candidate ranges locally using audio energy, transcript reactions, scene changes, and YouTube heatmap data when available.
- Send only compressed candidate previews to the selected Gemini model.
- Validate Gemini finish reasons and retry one incomplete structured response with a larger output budget across every selectable model.
- Apply hard daily limits for videos, source minutes, clips, and estimated AI cost.
- De-duplicate overlapping moments and render only the best candidates.
- Ask Gemini for the important scene focus and choose a subject crop, context-preserving frame, or facecam-plus-gameplay layout.
- Render 9:16 H.264 MP4 with scene-aware reframing and loudness normalization. Burned captions are disabled by default.
- Generate a relevant YouTube title, description, and tags for every newly analyzed candidate.
- Preview, approve, reject, regenerate, open, or trace a clip back to its source in the review queue.
- Permanently remove a review item and its local file from the right-click menu, including recovery from files deleted outside ClipRadar.
- Edit Gemini metadata, upload immediately, or choose a scheduled public release from the review flow.
- Persist upload progress, safely recover interrupted uploads, and track results in a compact publishing queue.
- Recover interrupted jobs safely after an application restart.
- Inspect up to 200 structured, attempt-grouped activity events in one selectable and copyable log view.
- Build and smoke-test a bundled Windows EXE through GitHub Actions.

## Security

The Gemini API key is entered only through **Settings → AI**. The Google OAuth client, refresh tokens, and resumable upload sessions are also stored through `keyring`; on Windows this uses the operating-system credential vault. These secrets are never stored in SQLite, project files, logs, Git, or the executable. Model, metadata, scheduling, and other non-secret settings are stored in SQLite.

## Connect YouTube for publishing

1. In Google Cloud, enable **YouTube Data API v3** for a project.
2. Configure its OAuth consent screen. While the app is in testing, add the Google account you will use as a test user.
3. Create an OAuth client with application type **Desktop app** and download its JSON file.
4. In ClipRadar, open **Settings → Publishing**, choose **Import OAuth JSON**, then **Connect YouTube**.
5. Complete Google sign-in in the browser and verify the displayed channel with **Test**.
6. In **Review**, select **Publish…** to edit Gemini's title, description, and tags, then upload now or schedule a public release.

Scheduled clips are uploaded as private first. YouTube owns the future release, so ClipRadar does not need to remain open after the upload completes. The local daily upload limit is configurable and defaults to 10.

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

Tests cover persistence and migration, secure-secret separation, channel baseline and analysis scheduling, local candidate selection, Gemini's structured metadata/framing schema, real FFmpeg focus and gaming-split rendering, the full pipeline through review, mocked resumable YouTube upload/scheduling, UI navigation, and the packaged executable.

## Runtime data

ClipRadar keeps its database, downloads, candidate previews, logs, and output in the platform application-data directory. The output folder can be changed in **Settings → Storage**. Candidate previews are deleted after analysis by default. Source downloads are kept by default so regeneration remains immediate.

## Architecture

The code is split by responsibility under `src/clipradar/`: `channels`, `monitoring`, `jobs`, `youtube`, `media`, `analysis`, `ai`, `rendering`, `review`, `publishing`, `settings`, `storage`, and `ui`. Network, Gemini, download, detection, rendering, OAuth, and upload operations run in a background executor; Qt's UI thread only coordinates and displays state.
