# Changelog

## 1.0.1 (2026-10-23)

- New `--measure` flag: prints a drawing's overall width and height in drawing units, so the agent can turn one known dimension into `--ref` for PNG/JPG, PDF and SVG.
- SKILL.md: ask for the known dimension before building raster/vector drawings, how to measure it, write outputs outside the skill folder, and clarify that `footprint_area_m2` is the area covered by walls.
- Self-test also checks `--measure` and a `--ref` build of the PNG sample.

## 1.0.0 (2026-10-23)

- First release: DXF, vector PDF, SVG and PNG/JPG to GLB, STL, OBJ, STEP, PNG preview, HTML viewer and quantity report.
