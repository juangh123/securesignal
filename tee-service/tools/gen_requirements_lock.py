# -*- coding: utf-8 -*-
"""
Regenerate requirements-lock.txt (hashed lockfile for the Docker build).

Usage (from repo root, pip>=23):

  1. Resolve the full dependency closure (any host OS works):
       pip install --dry-run --ignore-installed \
         -r tee-service/requirements.txt --report /tmp/report.json

  2. Emit the lock with linux/amd64 CPython 3.11 hashes from PyPI metadata:
       python tee-service/tools/gen_requirements_lock.py \
         /tmp/report.json tee-service/requirements-lock.txt

Notes:
  - Step 1 resolves on the host, so Windows-only marker deps (pywin32,
    colorama) may appear; they are dropped by DROP below.
  - Step 2 does not download wheels; it reads the PyPI JSON API and picks the
    file pip would select inside python:3.11-slim (Debian bookworm, glibc
    2.36), recording the best wheel hash plus the sdist hash as fallback.
"""
import json
import re
import sys
import time
import urllib.request

DROP = {"pywin32", "colorama"}  # platform_system == "Windows" marker only

def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()

def fetch(name, version):
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    req = urllib.request.Request(url, headers={"User-Agent": "securesignal-lockgen/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))

WHEEL_RE = re.compile(r"^(?P<name>.+?)-(?P<ver>.+?)-(?P<pytag>[^-]+)-(?P<abitag>[^-]+)-(?P<plattag>.+)\.whl$")

def wheel_rank(filename: str):
    """Sort key (higher = better) for linux/amd64 CPython 3.11, or None."""
    m = WHEEL_RE.match(filename)
    if not m:
        return None
    pytag, abitag, plattag = m.group("pytag"), m.group("abitag"), m.group("plattag")
    pyscore = -1
    for pt in pytag.split("."):
        if pt == "cp311":
            pyscore = max(pyscore, 400)
        elif pt.startswith("cp3") and abitag == "abi3":
            try:
                minor = int(pt[3:])
                if minor <= 11:
                    pyscore = max(pyscore, 300 + minor)
            except ValueError:
                pass
        elif pt in ("py3", "py2"):
            pyscore = max(pyscore, 100)
    if pyscore < 0:
        return None
    platscore = -1
    for pl in plattag.split("."):
        if pl == "any":
            platscore = max(platscore, 1)
        elif pl == "linux_x86_64":
            platscore = max(platscore, 2)
        else:
            mm = re.match(r"manylinux_(\d+)_(\d+)_x86_64$", pl)
            if mm and int(mm.group(2)) <= 36:  # bookworm glibc 2.36
                platscore = max(platscore, 100 + int(mm.group(2)))
            elif re.match(r"manylinux201[04]_x86_64$", pl):
                platscore = max(platscore, 114 if "2014" in pl else 110)
    if platscore < 0:
        return None
    return (pyscore, platscore)

def main():
    report_path = sys.argv[1] if len(sys.argv) > 1 else "report.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "requirements-lock.txt"
    report = json.load(open(report_path, encoding="utf-8"))
    items = sorted(
        {(norm(i["metadata"]["name"]), i["metadata"]["version"]) for i in report["install"]}
    )
    lines = []
    problems = []
    for name, version in items:
        if name in DROP:
            print(f"SKIP (windows-only): {name}=={version}")
            continue
        try:
            data = fetch(name, version)
        except Exception as e:
            problems.append(f"{name}=={version}: PyPI fetch failed: {e}")
            continue
        best, best_rank, sdist = None, None, None
        for f in data.get("urls", []):
            if f.get("yanked"):
                continue
            fn = f["filename"]
            if fn.endswith(".whl"):
                rk = wheel_rank(fn)
                if rk and (best_rank is None or rk > best_rank):
                    best, best_rank = f, rk
            elif fn.endswith((".tar.gz", ".zip")):
                sdist = f
        hashes = []
        if best:
            hashes.append(best["digests"]["sha256"])
        elif sdist:
            print(f"SDIST {name}=={version}: {sdist['filename']} (no linux wheel)")
        else:
            problems.append(f"{name}=={version}: no linux/amd64 py3.11 file found")
            continue
        if sdist and best:
            hashes.append(sdist["digests"]["sha256"])
        hash_str = " \\\n    ".join(f"--hash=sha256:{h}" for h in hashes)
        lines.append(f"{name}=={version} \\")
        lines.append(f"    {hash_str}")
        time.sleep(0.05)

    header = (
        "# requirements-lock.txt — hashed lockfile for reproducible Docker builds.\n"
        "# Target platform: linux/amd64, CPython 3.11 (python:3.11-slim, Debian bookworm).\n"
        "# Source of truth for versions: requirements.txt (top-level pins).\n"
        "# Regenerate from repo root (pip>=23, network access to PyPI):\n"
        "#   pip install --dry-run --ignore-installed -r tee-service/requirements.txt --report /tmp/report.json\n"
        "#   python tee-service/tools/gen_requirements_lock.py /tmp/report.json tee-service/requirements-lock.txt\n"
        "# Drop rules applied: pywin32 / colorama (Windows-only marker deps).\n"
    )
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(header + "\n" + "\n".join(lines) + "\n")
    print(f"\nWrote {out_path}: {len(lines)//2} packages")
    if problems:
        print("PROBLEMS:")
        for p in problems:
            print(" -", p)
        sys.exit(2)

if __name__ == "__main__":
    main()
