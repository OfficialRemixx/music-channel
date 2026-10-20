"""ONE-OFF DIAGNOSTIC v2 -- delete after use.

Probe v1 established: list/status/files all work, only `datasets
download` 404s on Kaggle CLI 2.2.4. That breaks both halves of storage
(sync needs the existing files to re-upload, B needs them to build a
mix). Find a download path that works.
"""
from __future__ import annotations

import os
import subprocess
import tempfile


def run(label, cmd, **kw):
    print(f"\n=== {label} ===\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600, **kw)
    print("rc:", r.returncode)
    print("stdout:", (r.stdout or "").strip()[:900])
    print("stderr:", (r.stderr or "").strip()[:900])
    return r


slug = f"{os.environ.get('KAGGLE_USERNAME', '')}/music-channel-tracks"

# Pick a real filename from the listing, which we know works.
files = subprocess.run(["kaggle", "datasets", "files", slug, "--csv"],
                       capture_output=True, text=True, timeout=300)
name = ""
for line in files.stdout.splitlines():
    if line.endswith(".wav") or ".wav," in line:
        name = line.split(",")[0].strip()
        break
print("picked filename:", name or "(none found)")

if name:
    d = tempfile.mkdtemp()
    run("single-file download", ["kaggle", "datasets", "download", "-d", slug, "-f", name, "-p", d])
    print("single-file result:", os.listdir(d))

run("pip install kaggle==1.6.17", ["pip", "install", "-q", "kaggle==1.6.17"])
run("version after pin", ["kaggle", "--version"])
d2 = tempfile.mkdtemp()
run("full download on 1.6.17", ["kaggle", "datasets", "download", "-d", slug, "-p", d2, "--unzip"])
print("1.6.17 full-download result:", len(os.listdir(d2)), "file(s)")
