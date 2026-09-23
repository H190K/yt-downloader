"""Real downloads with the tools in data/bin (``python -m pytest tests -q --run-network``).

* 12 jobs of one video (all MP4 qualities, all MP3 bitrates, M4A) into ONE folder, at most 4 at
  a time, then every output is verified with ffprobe.
* A job is cancelled mid-download while others (same video, same folder) keep going.
* The CLI (``python main.py --download ...``) produces the same verified output.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import REPO_ROOT
from tests.e2e.conftest import TEST_URL, Row, expected_height, ffprobe, leftovers, main_audio, main_video, record

pytestmark = pytest.mark.network

MAX_CONCURRENCY = 4
JOB_TIMEOUT = 30 * 60
MP4_QUALITIES = ("best", "2160", "1440", "1080", "720", "480", "360")
MP3_BITRATES = ("320", "256", "192", "128")


def _run_job(url: str, title: str, options, deps, cancel_after_fraction: float | None = None
             ) -> dict[str, Any]:
    """Run one DownloadJob to completion (blocking) and return what happened."""
    from core.engine import DownloadJob

    job = DownloadJob(url, title, options, deps)
    done = threading.Event()
    res: dict[str, Any] = {"job": job, "progress": 0, "max_fraction": 0.0}

    def on_progress(j, p):
        res["progress"] += 1
        f = p.get("fraction")
        if isinstance(f, (int, float)):
            res["max_fraction"] = max(res["max_fraction"], f)
            if cancel_after_fraction is not None and f >= cancel_after_fraction \
                    and not res.get("cancel_requested"):
                res["cancel_requested"] = time.monotonic()
                threading.Thread(target=j.cancel, daemon=True).start()

    def on_done(_j, ok, message):
        res["ok"], res["message"] = ok, message
        done.set()

    t0 = time.monotonic()
    job.start(on_progress, on_done)
    finished = done.wait(JOB_TIMEOUT)
    res["seconds"] = time.monotonic() - t0
    res["timed_out"] = not finished
    if not finished:
        job.cancel()
        job.wait(60)
    job.wait(30)
    return res


def _verify(test: str, kind: str, requested: str, path: str | None, deps, heights: list[int],
            job=None, seconds: float = 0.0, ok: bool | None = True, message: str = "") -> Row:
    """ffprobe-check one output file; returns a Row with any problems found."""
    row = Row(test=test, kind=kind, requested=requested, seconds=f"{seconds:.0f}")
    probs = row.problems
    if ok is not True:
        probs.append(f"job not ok: {message!r}")
    if not path or not os.path.isfile(path):
        probs.append(f"output file missing: {path!r} (message={message!r})")
        record(row)
        return row
    row.file = os.path.basename(path)
    row.size_mb = f"{os.path.getsize(path) / 1048576:.1f}"
    if not path.lower().endswith("." + kind):
        probs.append(f"wrong extension for {kind}: {row.file}")
    probe = ffprobe(path, deps)
    v, a = main_video(probe), main_audio(probe)
    row.vcodec = (v or {}).get("codec_name", "-")
    row.acodec = (a or {}).get("codec_name", "-")
    br = (a or {}).get("bit_rate")
    row.bitrate = f"{int(br) / 1000:.1f}k" if br else "?"

    if kind == "mp4":
        if not v:
            probs.append("no video stream")
        if not a:
            probs.append("no audio stream (MP4 must have sound)")
        if a and row.acodec not in ("aac", "mp3", "alac"):
            probs.append(f"audio codec {row.acodec} won't play in the Windows player")
        if v:
            side = min(int(v["width"]), int(v["height"]))
            row.delivered = f"{side}p"
            want = expected_height(requested, heights)
            if side != want:
                probs.append(f"delivered {side}p, expected {want}p for request {requested} "
                             f"(available {heights})")
            if not row.file.endswith(f" - {side}p.mp4"):
                probs.append(f"name suffix doesn't match delivered resolution {side}p: {row.file}")
    elif kind == "mp3":
        if v:
            probs.append("mp3 contains a real video stream")
        if row.acodec != "mp3":
            probs.append(f"audio codec {row.acodec}, expected mp3")
        if br:
            kbps = int(br) / 1000
            row.delivered = f"{kbps:.0f}kbps"
            if abs(kbps - int(requested)) > int(requested) * 0.05:
                probs.append(f"bitrate {kbps:.1f}k not within 5% of {requested}k")
        else:
            probs.append("ffprobe reported no audio bitrate")
        if not row.file.endswith(f" - {requested}kbps.mp3"):
            probs.append(f"name suffix should be ' - {requested}kbps.mp3': {row.file}")
    else:  # m4a
        if v:
            probs.append("m4a contains a real video stream")
        if row.acodec != "aac":
            probs.append(f"audio codec {row.acodec}, expected aac")
        m = re.search(r" - (\d+)kbps\.m4a$", row.file)
        if not m:
            probs.append(f"name suffix should be ' - <n>kbps.m4a': {row.file}")
        elif br:
            label, kbps = int(m.group(1)), int(br) / 1000
            row.delivered = f"{label}kbps"
            if abs(kbps - label) > label * 0.05:
                probs.append(f"name says {label}kbps but probed {kbps:.1f}k")
    if job is not None:
        dq = getattr(job, "delivered_quality", None)
        if not dq:
            probs.append("job.delivered_quality not set")
        elif f" - {dq}." not in row.file:
            probs.append(f"job.delivered_quality={dq!r} doesn't match the file name {row.file}")
        if getattr(job, "state", None) != "done":
            probs.append(f"job.state={getattr(job, 'state', None)!r}, expected 'done'")
    row.passed = not probs
    record(row)
    return row


def _options(kind: str, value: str, out_dir: Path):
    from core.engine import JobOptions

    if kind == "mp4":
        return JobOptions(kind="mp4", quality=value, out_dir=str(out_dir))
    if kind == "mp3":
        return JobOptions(kind="mp3", mp3_bitrate=value, out_dir=str(out_dir))
    return JobOptions(kind="m4a", out_dir=str(out_dir))


# ---------------------------------------------------------------- stress
def test_concurrent_all_formats_one_folder(real_deps, media_info, tmp_path):
    out = tmp_path / "stress"
    specs = ([("mp4", q) for q in MP4_QUALITIES] + [("mp3", b) for b in MP3_BITRATES]
             + [("m4a", "m4a")])
    in_flight = {"now": 0, "max": 0}
    lock = threading.Lock()

    def run(spec):
        with lock:
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
        try:
            return spec, _run_job(TEST_URL, media_info.title, _options(*spec, out), real_deps)
        finally:
            with lock:
                in_flight["now"] -= 1

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        results = list(pool.map(run, specs))

    assert in_flight["max"] <= MAX_CONCURRENCY
    rows, paths = [], []
    for (kind, value), res in results:
        job = res["job"]
        if res["timed_out"]:
            pytest.fail(f"{kind} {value} did not finish in {JOB_TIMEOUT}s")
        path = job.output_path if res.get("ok") else None
        paths.append(path)
        rows.append(_verify("stress", kind, value, path, real_deps, media_info.heights, job=job,
                            seconds=res["seconds"], ok=res.get("ok"), message=res.get("message", "")))

    problems = [f"{r.kind} {r.requested}: {p}" for r in rows for p in r.problems]
    # Same delivered quality = same file by design (e.g. "best" and "2160" on a 4K video): the
    # jobs may share a path only if the delivered quality matches and all but one of them report
    # already_downloaded (they reused the file instead of overwriting it).
    by_path: dict[str, list[int]] = {}
    for i, path in enumerate(paths):
        if path:
            by_path.setdefault(os.path.normcase(os.path.abspath(path)), []).append(i)
    for path, idx in by_path.items():
        if len(idx) < 2:
            continue
        jobs = [results[i][1]["job"] for i in idx]
        qualities = {getattr(j, "delivered_quality", None) for j in jobs}
        reused = sum(bool(getattr(j, "already_downloaded", False)) for j in jobs)
        if len(qualities) != 1 or reused < len(jobs) - 1:
            problems.append(f"several jobs reported the same output file: {os.path.basename(path)} "
                            f"(qualities={qualities}, already_downloaded={reused}/{len(jobs)})")
            for i in idx:
                rows[i].problems.append("same output file as another job (overwritten)")
                rows[i].passed = False
    on_disk = sorted(p.name for p in out.iterdir() if p.is_file())
    if len(on_disk) != len(by_path):
        problems.append(f"expected {len(by_path)} distinct files in the folder, found "
                        f"{len(on_disk)}: {on_disk}")
    left = leftovers(out)
    if left:
        problems.append(f"temp/partial files left behind: {left}")
    if (out / ".h190k-tmp").exists() and any((out / ".h190k-tmp").iterdir()):
        problems.append(f"scratch dir not cleaned: {list((out / '.h190k-tmp').iterdir())}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------- cancel
def test_cancel_one_job_others_survive(real_deps, media_info, tmp_path):
    out = tmp_path / "cancel"
    # Same video, same folder: the cancelled job's cleanup must not touch the others' files.
    victim_opts = _options("mp4", "2160", out)
    survivors = [("mp4", "360"), ("mp3", "128"), ("m4a", "m4a")]
    with ThreadPoolExecutor(max_workers=4) as pool:
        victim_f = pool.submit(_run_job, TEST_URL, media_info.title, victim_opts, real_deps, 0.05)
        surv_f = [pool.submit(_run_job, TEST_URL, media_info.title, _options(k, v, out), real_deps)
                  for k, v in survivors]
        victim = victim_f.result()
        surv = [f.result() for f in surv_f]

    problems: list[str] = []
    vjob = victim["job"]
    record(Row(test="cancel", kind="mp4", requested="2160 (cancelled)", file="-",
               passed=(victim.get("ok") is False and vjob.state == "cancelled"),
               seconds=f"{victim['seconds']:.0f}",
               problems=[] if vjob.state == "cancelled" else [f"victim state {vjob.state}"]))
    if not victim.get("cancel_requested"):
        problems.append(f"victim never reached 5% (max {victim['max_fraction']:.2f}); cancel not exercised")
    if victim.get("ok") is not False or victim.get("message") != "Cancelled.":
        problems.append(f"cancelled job reported ok={victim.get('ok')} message={victim.get('message')!r}")
    if vjob.state != "cancelled":
        problems.append(f"cancelled job state={vjob.state!r}")
    for (kind, value), res in zip(survivors, surv, strict=True):
        row = _verify("cancel", kind, value, res["job"].output_path if res.get("ok") else None,
                      real_deps, media_info.heights, job=res["job"], seconds=res["seconds"],
                      ok=res.get("ok"), message=res.get("message", ""))
        problems += [f"{kind} {value}: {p}" for p in row.problems]
    files = sorted(p.name for p in out.iterdir() if p.is_file())
    if any(" - 2160p" in f for f in files):
        problems.append(f"cancelled 2160p output still present: {files}")
    if len(files) != len(survivors):
        problems.append(f"expected {len(survivors)} files, found {files}")
    left = leftovers(out)
    if left:
        problems.append(f"temp/partial files left behind after cancel: {left}")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------- CLI
@pytest.mark.parametrize("cli_args,kind,requested", [
    (["--quality", "720"], "mp4", "720"),
    (["--mp3", "--bitrate", "192"], "mp3", "192"),
    (["--m4a"], "m4a", "m4a"),
])
def test_cli_download(real_deps, media_info, tmp_path, cli_args, kind, requested):
    out = tmp_path / f"cli-{kind}"
    cmd = [sys.executable, str(REPO_ROOT / "main.py"), "--download", TEST_URL, *cli_args, "--out", str(out)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    t0 = time.monotonic()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=JOB_TIMEOUT, cwd=str(REPO_ROOT), env=env, stdin=subprocess.DEVNULL)
    secs = time.monotonic() - t0
    tail = (proc.stdout[-1500:] + "\n--- stderr ---\n" + proc.stderr[-1500:])
    # "Saved (720p): C:\...\Title [id] - 720p.mp4"  (older CLI: "Saved: ...")
    m = re.search(r"^(?:Saved|Already downloaded)(?: \(([^)]*)\))?: (.+)$", proc.stdout, re.M)
    path = m.group(2).strip() if m else None
    printed_quality = m.group(1) if m else None
    files = sorted(p for p in out.iterdir() if p.is_file()) if out.is_dir() else []
    row = _verify("cli", kind, requested, path, real_deps, media_info.heights, seconds=secs,
                  ok=proc.returncode == 0, message=f"exit {proc.returncode}")
    problems = list(row.problems)
    if m is None:
        problems.append(f"no 'Saved ...: <path>' line in CLI output:\n{tail}")
    elif printed_quality and row.file and f" - {printed_quality}." not in row.file:
        problems.append(f"CLI printed quality ({printed_quality}) doesn't match file {row.file}")
    if proc.returncode != 0:
        problems.append(f"exit code {proc.returncode}:\n{tail}")
    if len(files) != 1:
        problems.append(f"expected exactly 1 file in {out}, found {[f.name for f in files]}")
    left = leftovers(out) if out.is_dir() else []
    if left:
        problems.append(f"temp/partial files left behind: {left}")
    assert not problems, "\n".join(problems)
