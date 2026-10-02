# Open Minis: Phone Audio Transcription and Traditional Chinese Summaries

[繁體中文](README.md) | English

Run Python scripts in the Open Minis Linux environment to read a folder of audio files. Groq Whisper produces original-language SRT and timestamped transcripts, then an app-authorized model generates Traditional Chinese Markdown summaries. Version 0.2.0 recommends GPT-6-Sol through the Minis model bridge by default; Groq text summaries remain an explicitly selected alternative.

Sources can include recordings exported from iPhone Voice Memos, folders exposed by third-party recording apps, or accessible Android recording folders. The scripts share the same options across these sources; no recording app name or Android vendor path is hardcoded.

**The workflow runs on your phone, but transcription and summarization use cloud models. An internet connection and your own Groq API key are required.** Audio chunks go to Groq. Minis summaries send transcript text to your selected model service; Groq summaries send transcripts and intermediate summaries to Groq. Files are processed sequentially through regular API calls, not the Groq Batch API. The scripts do not watch folders on a schedule.

For long jobs, keep Open Minis in the foreground. On iPhone, you can also enable **Enhanced Background Execution** under **Settings → Permissions → Background**, then configure the related options and permissions as prompted by the app. This helps tasks continue after switching apps, but does not guarantee completion with the screen locked. If the OS suspends the app or terminates the process, rerun with the same options. Menu locations and available options depend on your installed version. Official implementation: [iOS background settings](https://github.com/OpenMinis/OpenMinis/blob/main/src/ios/Views/Settings/EnhancedBackgroundSettingsView.swift).

## Test status

The public version is `0.2.0`, adding app-owned model invocation, completion checks, and paced hierarchical Groq summaries. The author's private iPhone workflow successfully summarized an approximately 81-minute recording with 41,765 input tokens using GPT-6-Sol in one request. This is not an end-to-end phone test of this public version. See the test coverage document for the distinction.

Offline regression tests and synthetic M4A/WAV audio tests are included. See [test coverage](docs/TESTING.md) for results and outstanding validation items. The supporting documents are currently in Traditional Chinese.

## 1. Installation and preparation

1. Use the installation links for your platform on the [Open Minis official website](https://openminis.app/) or its [official source repository](https://github.com/OpenMinis/OpenMinis).

2. Download the ZIP from the [latest full release](https://github.com/taoyutsun/openminis-groq-transcription-workflow/releases/latest). Put all five scripts, `groq_workflow.py`, `groq_transcribe.py`, `groq_http.py`, `groq_summary.py`, and `minis_summary.py`, in the same folder. The examples use `/var/minis/shared/錄音轉錄工具/`. Update all five together and preserve the previous scripts and output before upgrading.

3. In the Open Minis terminal, check that Python 3.8 or newer and FFmpeg/ffprobe are available. In an Alpine environment, install missing packages with:

   ```sh
   apk add python3 ffmpeg ca-certificates
   ```

   If package installation is restricted, use the installation method supported by your Open Minis version. FFmpeg must support the source format; it is preferred for M4A/AAC. SoX is an optional fallback, subject to its installed decoder support.

4. Create your own API key in the [Groq Console](https://console.groq.com/keys) and add `GROQ_API_KEY` in the Open Minis environment-variable settings. A key configured for a chat provider may not automatically be available to the shell. Do not paste keys into conversations, source files, or GitHub.

5. Configure or sign in to your selected model service in Open Minis. For Codex OAuth GPT-6-Sol, add **GPT-6-Sol** under **Settings → Model Groups → Available Models in Agent Loop** (the Chinese UI reads **Minis 執行時可調用的模型**). Selecting the default chat model alone is insufficient. Do not export OAuth tokens or use them as API keys.

6. Create or mount your audio source folder and verify that it is readable from the Open Minis terminal.

Start with a local environment check:

```sh
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --check
```

This reports audio tools, whether a key is configured, and whether the Minis summary model is exposed in the local allowed list. It does not reveal keys or validate quotas or actual inference access. Use `--minis-provider 'your provider label'` to disambiguate duplicate model entries.

To use Groq instead, or if your app version lacks the required bridge format, explicitly add `--summary-backend groq`, including when checking setup. Set `SUMMARY_BACKEND=groq` to retain the v0.1.0 backend choice. The workflow never silently falls back to another service.

The examples retain the Chinese folder names used by the default configuration: `錄音轉錄工具` (scripts), `錄音輸入` (audio input), and `錄音逐字稿` (output). You can use other names with `--source` and `--output`.

## 2. iPhone audio sources

### Apple Voice Memos: export to a dedicated folder

1. Select a recording in Voice Memos, then choose **More → Share → Save to Files**.
2. Choose the single-file/rendered M4A sharing format. Editable multilayer recordings are outside this version's test scope.
3. Save to a dedicated `錄音輸入` folder accessible in Files. If the Open Minis shared location is available, save there and verify that its Linux path is `/var/minis/shared/錄音輸入/`.
4. For other locations, select the folder in the Open Minis external-folder mounting settings and note the Linux path shown by the app, for example `/var/minis/mounts/錄音輸入/`. Use the actual name and path of your mount.
5. Keep output in a separate folder from your source recordings.

Folders used to organize recordings inside Voice Memos are not directly mountable folders in Files. This workflow uses Apple's sharing/export feature, without accessing the app's internal storage. Make sure iCloud files have been downloaded to the phone and can actually be read.

Apple documentation: [Export a recording to Files](https://support.apple.com/guide/iphone/iph831c37815/ios).

### Third-party recording apps

If a recording app exposes its audio folder in Files, mount it and pass its path with `--source`. Otherwise, share/export recordings to the dedicated input folder. Recorder Pro is one possible source, not a required app.

## 3. Android audio sources

First locate the recordings in your file manager. For example, Samsung's documentation shows **Internal storage → Recordings → Voice Recorder**. Other vendors, OS versions, and recording apps may use different locations.

Select the actual recording folder in the Open Minis external-folder mounting settings, grant access, and use the Linux path shown by the app:

```sh
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --source '/var/minis/mounts/Voice Recorder' --list
```

The file manager's display path is not the script's Linux path. Do not assume `/storage/emulated/0/...` is readable inside Open Minis. Mount the folder and list its contents to verify access.

If the recordings are in app-private storage, the folder cannot be selected, a cloud provider does not support direct mounting, or the recording app does not expose its storage location, share/export the audio to a dedicated local input folder and mount that instead. This is also a more repeatable approach across vendors.

Official references: [Samsung recording storage and sharing](https://www.samsung.com/my/support/mobile-devices/how-to-record-play-back-and-share-voice-recordings-on-your-samsung-galaxy-phone/), [Android folder-access permissions](https://developer.android.com/training/data-storage/shared/documents-files), and [Open Minis Android mounting UI source](https://github.com/OpenMinis/OpenMinis/blob/main/src/android/app/src/main/java/com/openminis/app/ui/settings/MountedFoldersScreen.kt).

Use a short, non-sensitive recording first to verify mounting, decoding, and output access.

Android can use Groq transcription and Groq summaries with `--summary-backend groq`. The Minis backend requires the supported JSON envelope, model identity, and completion status. At review time, the official Android text bridge differed from iOS and did not report the same completion status; this version does not weaken validation to claim success. Run `--check` first and explicitly select Groq if incompatible. Android physical-device testing is still outstanding.

## 4. Select files and run

These commands assume input at `/var/minis/shared/錄音輸入/`. If you mounted a different folder, add `--source 'actual mounted path'`.

```sh
# List files: no key, decoding, upload, or writes
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --list

# Preview up to one pending file: no upload or writes
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --dry-run

# Run: process at most one pending file by default
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py'

# English recording: English SRT and Traditional Chinese summary
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --file '英文課程.m4a' --language en

# Explicit selection: repeat --file for each recording
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --file '課程一.m4a' --file '訪談二.mp3'

# All files not completed with matching transcription/summary settings
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --all

# Include source subfolders only when needed
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --all --recursive

# Custom input and output
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --source '/var/minis/mounts/錄音輸入' \
  --output '/var/minis/shared/錄音逐字稿' --dry-run
```

`--all` selects all pending files; `--file` processes all explicitly selected files. Other modes default to `--limit 1`. Source files are read only, never moved or deleted. Files are sorted by path, not recording time.

You can also set `GROQ_SOURCE_DIR` and `GROQ_OUTPUT_DIR` as defaults. Command-line options take precedence.

### Starting from an Open Minis conversation

Tell Open Minis the actual script and source locations, for example:

> My workflow is at `/var/minis/shared/錄音轉錄工具/groq_workflow.py`, and my audio source is `/var/minis/mounts/錄音輸入`. First run `--list`, then use `--dry-run` to preview the files I select.

> Run this workflow for “英文課程.m4a” only, with `--language en`, and save output to `/var/minis/shared/錄音逐字稿`.

> Check that GPT-6-Sol is exposed in Available Models in Agent Loop, then use the default Minis summary backend for my selected recordings. Stop and report any model failure; do not switch services.

> This recording is already transcribed. Use `--summary-only` to summarize it with GPT-6-Sol, without uploading the audio again. Replace the old summary only after success.

> I explicitly choose the Groq alternative this time. Add `--summary-backend groq`, preview the selected files, then run.

Your current Open Minis chat model interprets and executes these instructions. The Python scripts are not an Open Minis plugin and do not bind themselves to a particular conversation title.

## 5. Language and summary settings

- `--language auto`: the default; no fixed language is sent to the transcription API. Useful as a starting point for mixed Chinese/English names or an unknown language, but accurate sentence-by-sentence language switching is not guaranteed.
- `--language en`: primarily English.
- `--language zh`: primarily Chinese.
- SRT uses the transcription endpoint and retains the recognized language. No translation endpoint is called.
- Default transcription model: `whisper-large-v3-turbo`. Override with `--model whisper-large-v3`.
- Default summary backend: `--summary-backend minis`, with `--minis-model gpt-6-sol`. Resolve exact entries from the app's allowed list; do not follow the chat model or hardcode the author's provider ID. Configure `MINIS_SUMMARY_MODEL`, `MINIS_SUMMARY_PROVIDER`, or their CLI equivalents for your own entry.
- Explicit Groq alternative: `--summary-backend groq`. This defaults to `openai/gpt-oss-120b`; `--summary-model` affects Groq only.
- Minis defaults: `--minis-max-tokens 6000` and `--minis-timeout 600` seconds. These are limits, not length/speed guarantees. Output-limit responses are rejected rather than recorded as complete.
- `--summary-style original` preserves the author's original summary prompt. The optional `cautious` mode additionally asks the model to mark uncertain names, terms, and numbers as “待核對” (needs verification).

```sh
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --file '課程一.m4a' --summary-style cautious
```

Minis reads the entire transcript in one request within a conservative input budget. Authentication stays in the app. UTF-8 byte count is used as a conservative token upper bound, with output and framing reserved against the listed context window. Missing context metadata uses a smaller budget. This is not actual tokenizer counting and may reject inputs the model could accept. Oversized inputs fail explicitly; select Groq or split files yourself.

Groq uses smaller sections and a balanced hierarchy, persisting completed nodes. Requests are spaced at least 60 seconds apart; estimated input including the prompt is bounded around 3,500 tokens, with 2,000 output tokens reserved. Ordinary 429 responses wait/retry; oversized or truncated requests attempt smaller sections. Estimates cannot guarantee account limits or completion. [Groq's free-tier table](https://console.groq.com/docs/rate-limits) lists 8,000 TPM for gpt-oss-120b; tokens are not characters, and sufficient daily quota does not prevent minute-level throttling.

Both backends summarize recognized text rather than directly understanding the full recording. They can omit details or amplify transcription errors. Cautious mode cannot guarantee correctness.

## 6. Output and resuming

Each recording gets a `filename__identifier/` folder containing SRT, `_逐字稿.txt` (transcript), `_摘要.md` (summary), `metadata.json`, `chunk_*.json`, and `摘要設定.json` (summary settings). Groq hierarchical summaries additionally use `摘要進度.json` (summary progress).

The identifier incorporates the audio-content SHA-256, language, transcription model, decoder, and chunking settings. Summary caching separately checks the transcript-content SHA-256, full prompt, summary model, and style.

- Same settings: reuse valid completed chunks and section summaries; skip completed files.
- Corrupt chunk JSON: regenerate only invalid chunks or chunks with mismatched settings.
- Different summary model/style: summarize the existing complete transcript and preserve the previous summary version, without uploading audio again.
- Different language, transcription model, decoder, or audio content: create a different output folder.
- `--force`: create a new folder and transcribe again; cannot be combined with `--summary-only`.
- `--summary-only`: use only fully transcribed results from this public version, not incomplete transcripts.

Complete v0.1.0 public transcription results are reusable; changing the summary backend does not transcribe again. The private phone version uses different identifiers/cache formats and is not automatically migrated. Preserve those results and test the public version separately. Interrupted Minis single-pass summaries resend that request, but keep completed transcription. Groq resumes saved hierarchy nodes.

Run only one workflow instance at a time. If iOS suspends the app, Android power management interrupts it, or the process is terminated, manually rerun with the same options. The current chunk may need to be uploaded again if its result was not saved.

## 7. Privacy, limitations, and license

- Audio is sent to Groq as approximately four-minute, slightly overlapping, 16 kHz mono FLAC chunks. One chunk's HTTP upload body is assembled in memory at a time; the entire long recording is not loaded into memory.
- Automatic extension filtering includes MP3, M4A, WAV, FLAC, OGG, MP4, AAC, and WEBM. Actual decoding depends on FFmpeg/SoX support on the device. Listing an extension does not mean it has been tested on a physical phone.
- Overlap merging uses timestamp cutoffs; repeated or missing words may still occur at chunk boundaries.
- No speaker diarization, live transcription, automatic Voice Memos export, or continuous folder watching.
- HTTP 429 and temporary server/connection failures are retried up to four times. Not every quota restriction can be resolved automatically.
- Groq's free tier can be used for trials, but account limits for requests, audio duration, and text tokens still apply. Chunking does not remove those limits. Paid plans may incur charges; check [Groq rate limits](https://console.groq.com/docs/rate-limits) and your billing settings first.
- Minis summaries remain subject to the selected service, authentication method, and account limits. Codex OAuth does not promise universal GPT-6-Sol access or unlimited free usage. API-key access and ChatGPT sign-in are different billing routes. This workflow does not sign in, extract OAuth credentials, or switch authentication methods for you.
- Review specialist terms, names, numbers, and accents manually. Only use recordings you are authorized to send to a cloud service.
- The key is used by Python HTTP requests, not passed in external-process arguments. Errors omit HTTP response bodies and include key redaction. You still need to control access to terminals, the agent, environment variables, and device backups.
- Do not add recordings, transcripts, caches, full development conversations, environment settings, or private device data to the public project. See [public scope and data flow](docs/PRIVACY.md).

Author: Arthur Tao. The code is shared under the [MIT License](LICENSE). Open Minis, Groq, and the models have their own licenses and terms. This is an independent user-created workflow.

Related article (Traditional Chinese): [What is Open Minis? A mobile agent that lets cloud AI models use native phone tools](https://taoyutsun.blogspot.com/2026/08/open-minis-cloud-ai-mobile-agent.html).
