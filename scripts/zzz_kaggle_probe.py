"""ONE-OFF DIAGNOSTIC -- delete after use.

`kaggle datasets download` has 404'd in every A1c run while `create` and
`version` clearly work (the dataset holds 41 files). The slug-resolution
fix logged nothing, which means the env-derived ref already matched, so
the ref is NOT the problem. Find out what the CLI actually sees.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile


def run(label, cmd):
    print(f"\n=== {label} ===\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    print("rc:", r.returncode)
    print("stdout:", (r.stdout or "").strip()[:2000])
    print("stderr:", (r.stderr or "").strip()[:2000])
    return r

user = os.environ.get("KAGGLE_USERNAME", "")
print("KAGGLE_USERNAME length:", len(user), "| lowercase == itself?", user == user.lower())
slug = f"{user}/music-channel-tracks"

run("kaggle --version", ["kaggle", "--version"])
run("datasets list --mine", ["kaggle", "datasets", "list", "--mine", "--csv"])
run("datasets status", ["kaggle", "datasets", "status", slug])
run("datasets files", ["kaggle", "datasets", "files", slug, "--csv"])
dest = tempfile.mkdtemp()
run("datasets download", ["kaggle", "datasets", "download", "-d", slug, "-p", dest, "--unzip"])
print("\nfiles downloaded:", len(os.listdir(dest)))
sys.exit(0)
