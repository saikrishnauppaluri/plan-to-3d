---
name: plan-to-3d
description: Turn a 2D drawing into a 3D model. Use when the user uploads or points to a floor plan, site plan, part profile, logo outline, or mechanical part drawing (DXF, vector PDF, SVG, PNG, JPG) and wants a 3D model, a 3D preview, GLB/STL/OBJ/STEP files, or wall, room and material quantities from it.
---

# Plan to 3D

Convert a 2D drawing into a 3D model and send back the files, a preview image, and an approximate quantity summary. Act like a practical CAD technician: short, specific, no hype.

All paths below are relative to this skill's directory (the folder containing this SKILL.md).

## Setup (once per machine)

```bash
python3 -m venv .venv && .venv/bin/pip install -r scripts/requirements.txt
.venv/bin/python scripts/selftest.py   # must print ALL PASS
```

`build123d` is only needed for STEP export and mechanical parts; if it is missing the converter skips STEP and says so.

## 1. Identify the drawing

Decide which kind it is, or ask only if you truly cannot tell:

| Drawing | `--kind` | What happens |
|---|---|---|
| Floor plan or site plan | `plan` (default) | Lines become walls of `--wall` thickness (default 0.20 m), extruded to `--height` (default 3.0 m). Gaps in walls stay open as doors. A DXF layer named WALL, WALLS or A-WALL is used automatically. |
| Profile, logo, flat part outline | `profile` | Closed outlines are filled (inner loops become holes) and extruded to `--height`, the thickness the user gives. |
| Mechanical drawing with front/top/side views | see section 4 | Built by hand with build123d after the user confirms the dimensions. |

## 2. Get the scale (never guess it)

- **DXF**: units come from the file's `$INSUNITS`. If it has none, the converter assumes metres and says so; check the report's size looks plausible.
- **Vector PDF, SVG, PNG/JPG**: the converter needs a scale. Unless the drawing states one, stop and ask the user for one known dimension (for example "the long outside wall is 10.2 m") before building. Then measure that length in drawing units (PDF points, SVG px, or image pixels):
  ```bash
  .venv/bin/python scripts/plan_to_3d.py <drawing> --measure   # prints overall width and height in drawing units
  ```
  For an overall outside dimension, use the printed width or height directly for images (they include the wall faces); for single-line vector drawings subtract one wall thickness from the real length, because lines are wall centrelines. Then pass `--ref <units> <metres>`. Example: a PNG measuring 511 px wide whose long wall is 10.2 m becomes `--ref 511 10.2`. If the drawing states a scale (for example 1:100 on an A-series sheet), `--scale` is metres per drawing unit (1:100 in PDF points = 0.0352778).
- **Scans, photos, hand sketches**: warn that the result will be rough.
- **DWG**: not supported directly. Ask the user to export DXF (AutoCAD: Save As > DXF).

Ask at most one question per drawing, and only when the answer changes the model (scale, wall height, thickness, or how views line up). Otherwise build straight away and state your assumptions.

## 3. Convert

```bash
.venv/bin/python scripts/plan_to_3d.py <drawing> [--kind plan|profile] [--height M] [--wall M] \
    [--scale M_PER_UNIT | --ref UNITS METRES] [--layer NAME] [--door-gap M] [--html] --out <dir>
```

Write `--out` to a working folder outside the skill directory (for example next to the user's file). Outputs in `<dir>`, named after the input:

- `.glb`: opens in a browser, Windows 3D Viewer, and most AR viewers (Y-up)
- `.stl`: 3D printing (Z-up, metres)
- `.obj` + `.mtl`: Blender, SketchUp, and other modelling apps
- `.step`: Fusion, SolidWorks, FreeCAD
- `.png`: preview render
- `.html` (with `--html`): one-file 3D viewer with orbit, zoom and pan; it loads three.js from a CDN, so it needs internet
- `.report.json`: size, watertight check, footprint area, volume, wall centreline length, each enclosed room's area, approximate material estimate (concrete m³ if cast; brick count at 190×90×90 mm with 10 mm joints, change with `--brick L W H`), assumptions and warnings

## 4. Mechanical drawings (front/top/side views)

1. Read every dimension you can see. List them and how you think the views line up, and ask the user to confirm in one message.
2. Never invent a dimension you cannot read; ask for it.
3. After confirmation, write a short build123d script (sketch the profile from one view, extrude, then cut features from the other views), export STEP and STL, and check the solid's bounding box against the drawing's overall dimensions.

## 5. Check before sending

- `watertight` is true in the report (required for 3D printing).
- The model's overall size matches the drawing's overall dimensions; look at `warnings` for scale-check flags.
- Open the `.png` preview and look at it. Walls should be where the drawing has them and door gaps should be open.
- Room areas should be plausible. They are measured inside the walls, so they will be smaller than gross or sheet areas that include walls.

## 6. Reply

Send the files, the preview, and a short summary: overall size, wall centreline length, wall footprint area (`footprint_area_m2` is the floor area covered by the walls themselves, not the building's floor area), wall volume, the area of each room, and the material estimate. Say in one line which file to use for what. List every assumption (for example "3 m wall height assumed"). Label every quantity **approximate, for planning only**.

## Limits (say so in one line if asked)

- It does not draw new plans from scratch, render photoreal interiors, or do general CAD help.
- Quantities are not a structural design, an engineering approval, or a tender bill of quantities.
- Do not upload the user's drawings to outside websites or services; all processing is local.
