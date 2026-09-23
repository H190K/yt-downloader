# Tests

```
tests/
  conftest.py            --run-network option, "network" marker, repo root on sys.path
  unit/                  fast, offline (< 2 s): no network, no real tools, tmp dirs only
    test_build_args.py     yt-dlp command line per kind/quality/bitrate/playlist + name suffix
    test_engine_helpers.py normalize_url, friendly_error, resolution labels, output parsing
    test_ui_util.py        quality_options / quality_label / preferred_quality, URL helpers
    test_config.py         defaults, sanitising, legacy migration, atomic save
    test_deps.py           version parsing/compare, versions.json, checksums, cleanup_temp
  e2e/                   real downloads (marked "network"), skipped unless --run-network
    test_stress_download.py
```

## Setup

```
pip install -r requirements-dev.txt     # or just: pip install pytest Pillow
```

`ui.util` imports Pillow, so the unit tests need it too.

## Run

```
python -m pytest tests/unit -q                 # unit tests only (what CI should run)
python -m pytest tests -q                      # everything; network tests are reported as skipped
python -m pytest tests -q --run-network        # + real downloads (10-30 min, ~700 MB)
python -m pytest tests/e2e -q --run-network -k cli     # just the CLI checks
```

Run from the repository root. The network tests need the tools in `data/bin`
(`python main.py --setup` installs them) and internet access.

## What the network tests check

Video: <https://youtu.be/5Y3a06rJjoU> (2.5 min, 144p-2160p).

* **Stress** (`test_concurrent_all_formats_one_folder`): MP4 best/2160/1440/1080/720/480/360,
  MP3 320/256/192/128 and M4A, all into one folder, at most 4 at a time. For every job:
  `on_done` ok, the file exists, all 12 names are distinct, and ffprobe shows
  * MP4: a video and an AAC/MP3/ALAC audio stream; the smaller side of the video equals the
    requested height (or the highest available below it); the name ends in ` - <that>p.mp4`;
  * MP3: an mp3 stream within 5 % of the requested bitrate; name ends in ` - <n>kbps.mp3`;
  * M4A: an aac stream; name ends in ` - <n>kbps.m4a` matching the probed bitrate (5 %);
  * `job.delivered_quality` matches the name suffix;
  * no `.part`/`.ytdl`/`.fNNN.*`/thumbnail/scratch (`.h190k-tmp`) files remain.
* **Cancel** (`test_cancel_one_job_others_survive`): a 2160p job is cancelled at ~5 % while
  a 360p MP4, an MP3 and an M4A of the same video download into the same folder; the cancelled
  job reports `Cancelled.` and leaves nothing behind, the others pass the checks above.
* **CLI** (`test_cli_download`): `python main.py --download URL --quality 720 --out DIR`
  (plus `--mp3 --bitrate 192` and `--m4a`) exits 0 and produces one verified file.

At the end a results table (file, requested, delivered, vcodec, acodec, bitrate, size, time,
PASS/FAIL) is printed in the pytest summary. Set `H190K_RESULTS_JSON=path.json` to also save it.

## Debugging failures

* Unit failure: `python -m pytest tests/unit -q -x --tb=long -k <name>`.
* Network failure: the assertion message lists every problem per job (e.g. delivered 1080p
  expected 1440p, leftover files). To reproduce by hand:
  `python main.py --download https://youtu.be/5Y3a06rJjoU --quality 1440 --out %TEMP%\h190k`
  and inspect with `data\bin\ffprobe.exe -hide_banner <file>`.
* Network tests depend on YouTube: a one-off failure with a network/rate-limit message
  should be re-run once before being reported as a bug. Do not add automatic retries.
* The pytest `tmp_path` folders (with the downloaded files) are kept for the last 3 runs under
  `%TEMP%\pytest-of-<user>\`.
