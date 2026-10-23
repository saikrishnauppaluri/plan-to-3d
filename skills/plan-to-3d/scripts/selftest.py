#!/usr/bin/env python3
"""End-to-end self-test: converts the bundled samples and checks the results.
Usage: python selftest.py [OUT_DIR]   (run from anywhere; exits non-zero on failure)"""
import json, os, subprocess, sys
here = os.path.dirname(os.path.abspath(__file__))
samples = os.path.join(here, "..", "samples")
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.getcwd(), "plan-to-3d-selftest")
conv = os.path.join(here, "plan_to_3d.py")
cases = [
    ("dxf", ["sample_plan.dxf", "--html"], [39.44, 12.54, 12.54]),
    ("pdf", ["sample_plan.pdf", "--ref", "28.3465", "1.0"], [39.44, 12.54, 12.54]),
    ("png", ["sample_plan.png", "--scale", "0.02"], [39.44, 12.54, 12.54]),
    ("png-ref", ["sample_plan.png", "--ref", "511", "10.2"], [39.44, 12.54, 12.54]),
    ("svg", ["sample_profile.svg", "--kind", "profile", "--height", "0.005", "--scale", "0.001"], None),
]
ok = True
m = subprocess.run([sys.executable, conv, os.path.join(samples, "sample_plan.png"), "--measure"], capture_output=True, text=True)
try:
    mw = json.loads(m.stdout)["width"]
    good = abs(mw - 511) <= 3
except Exception:
    mw, good = m.stderr.strip()[-200:], False
print(f"[{'PASS' if good else 'FAIL'}] measure: sample_plan.png width {mw} px (expected ~511)")
ok &= good
for tag, args, rooms in cases:
    d = os.path.join(out, tag)
    args = [os.path.join(samples, args[0])] + args[1:]
    r = subprocess.run([sys.executable, conv, *args, "--out", d], capture_output=True, text=True)
    if r.returncode:
        print(f"[FAIL] {tag}: {r.stderr.strip()[-300:]}"); ok = False; continue
    rep = json.loads(r.stdout)
    problems = []
    if not rep["watertight"]: problems.append("mesh not watertight")
    for f in rep["files"]:
        if not os.path.getsize(os.path.join(d, f)): problems.append(f"empty {f}")
    if rooms:
        got = [x["area_m2"] for x in rep["rooms"]]
        if len(got) != len(rooms) or any(abs(g - e) / e > 0.03 for g, e in zip(got, rooms)):
            problems.append(f"room areas {got} vs expected {rooms}")
    if tag == "svg" and abs(rep["size_m"]["x"] - 0.09) > 0.002: problems.append("profile size wrong")
    print(f"[{'FAIL' if problems else 'PASS'}] {tag}: {', '.join(rep['files'])}" + (f"  -> {problems}" if problems else ""))
    ok &= not problems
print("ALL PASS" if ok else "SOME CHECKS FAILED", "| outputs in", out)
sys.exit(0 if ok else 1)
