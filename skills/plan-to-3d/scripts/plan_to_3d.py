#!/usr/bin/env python3
"""plan_to_3d.py - turn a 2D drawing into a 3D model.

Inputs : DXF (best), vector PDF, SVG, PNG/JPG (raster plans, scans, photos).
Kinds  : plan    - floor/site plan: lines become walls of --wall thickness, extruded to --height.
         profile - closed outline (logo, part profile): filled and extruded to --height (thickness).
Outputs: GLB, STL, OBJ, STEP (if build123d is installed), PNG preview, report.json,
         and optionally a single-file HTML 3D viewer (--html).

Every estimate in report.json is approximate and for planning only.
"""
import argparse, base64, json, math, os, sys
import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon, MultiPolygon
from shapely.ops import unary_union, polygonize

DXF_UNITS = {0: None, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0, 14: 0.1}  # $INSUNITS -> metres


def log(*a):
    print(*a, file=sys.stderr)


# ---------------------------------------------------------------- readers
def read_dxf(path, layer=None):
    import ezdxf
    from ezdxf import path as ezpath
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    ents = list(msp.query("LINE LWPOLYLINE POLYLINE ARC CIRCLE SPLINE ELLIPSE"))
    layers = {e.dxf.layer for e in ents}
    wall_layer = layer or next((l for l in layers if l.upper() in ("WALL", "WALLS", "A-WALL")), None)
    notes = []
    if wall_layer:
        ents = [e for e in ents if e.dxf.layer == wall_layer]
        notes.append(f"used only DXF layer '{wall_layer}'")
    lines = []
    for e in ents:
        try:
            p = ezpath.make_path(e)
            pts = [(v.x, v.y) for v in p.flattening(0.01)]
        except Exception:
            continue
        if len(pts) >= 2:
            lines.append(pts)
    unit = DXF_UNITS.get(doc.header.get("$INSUNITS", 0))
    return lines, unit, notes


def read_pdf(path, page_no=0):
    import pymupdf
    doc = pymupdf.open(path)
    page = doc[page_no]
    lines = []
    for d in page.get_drawings():
        for it in d["items"]:
            op = it[0]
            if op == "l":
                lines.append([(it[1].x, -it[1].y), (it[2].x, -it[2].y)])
            elif op == "re":
                r = it[1]
                lines.append([(r.x0, -r.y0), (r.x1, -r.y0), (r.x1, -r.y1), (r.x0, -r.y1), (r.x0, -r.y0)])
            elif op == "qu":
                q = it[1]
                lines.append([(p.x, -p.y) for p in (q.ul, q.ur, q.lr, q.ll, q.ul)])
            elif op == "c":
                lines.append([(it[1].x, -it[1].y), (it[4].x, -it[4].y)])
    if not lines:
        raise SystemExit("No vector lines found in the PDF (it may be a scan). Export it as PNG and run again with --scale.")
    return lines, None, [f"read {len(lines)} vector paths from PDF page {page_no + 1} (units: PDF points)"]


def read_svg(path):
    from svgelements import SVG, Shape
    svg = SVG.parse(path)
    lines = []
    for el in svg.elements():
        if isinstance(el, Shape):
            from svgelements import Path
            p = Path(el)
            for sub in p.as_subpaths():
                sp = Path(sub)
                L = sp.length(error=1e-3)
                if not L:
                    continue
                n = max(2, int(L / 2) + 1)
                pts = [sp.point(t) for t in np.linspace(0, 1, n)]
                lines.append([(pt.x, -pt.y) for pt in pts])
    return lines, None, [f"read {len(lines)} SVG subpaths (units: SVG px)"]


def read_raster(path, scale):
    import cv2
    g = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if g is None:
        raise SystemExit(f"Cannot read image {path}")
    _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    cs, hier = cv2.findContours(b, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    polys = []
    H = g.shape[0]
    tf = lambda c: [(x * scale, (H - y) * scale) for x, y in c[:, 0]]
    for i, c in enumerate(cs):
        if hier[0][i][3] == -1 and len(c) > 2:
            holes = [tf(cs[j]) for j in range(len(cs)) if hier[0][j][3] == i and len(cs[j]) > 2]
            polys.append(Polygon(tf(c), holes).buffer(0))
    geom = unary_union([p for p in polys if p.area > (3 * scale) ** 2])
    return geom.simplify(scale * 1.5), [f"traced {len(polys)} filled regions from the image (Otsu threshold)"]


# ---------------------------------------------------------------- geometry
def polys_of(g):
    if g.is_empty:
        return []
    if isinstance(g, Polygon):
        return [g]
    return [p for p in getattr(g, "geoms", []) if isinstance(p, Polygon) and not p.is_empty]


def walls_from_lines(lines, k, wall):
    segs = [LineString([(x * k, y * k) for x, y in pts]) for pts in lines]
    segs = [s for s in segs if s.length > 1e-6]
    centre_len = sum(s.length for s in segs)
    geom = unary_union([s.buffer(wall / 2, cap_style=2, join_style=2) for s in segs])
    return geom, centre_len


def profile_from_lines(lines, k):
    segs = [LineString([(x * k, y * k) for x, y in pts]) for pts in lines]
    faces = list(polygonize(unary_union(segs)))
    if not faces:
        raise SystemExit("No closed outline found; profile mode needs closed shapes.")
    # even-odd fill: XOR all faces so inner loops become holes
    g = faces[0]
    for f in faces[1:]:
        g = g.symmetric_difference(f)
    return g.buffer(0)


def rooms_of(walls, close_gap):
    """Enclosed spaces. Walls are rasterised, door gaps up to close_gap metres are closed with a
    square kernel, and every free region not connected to the outside counts as a room."""
    import cv2
    x0, y0, x1, y1 = walls.bounds
    res = max(0.01, max(x1 - x0, y1 - y0) / 4000.0)
    pad = int(close_gap / res) + 4
    w, h = int((x1 - x0) / res) + 2 * pad, int((y1 - y0) / res) + 2 * pad
    img = np.zeros((h, w), np.uint8)
    for p in polys_of(walls):
        ring = lambda c: np.array([[(x - x0) / res + pad, (y - y0) / res + pad] for x, y in c.coords], np.int32)
        cv2.fillPoly(img, [ring(p.exterior)], 255)
        for hole in p.interiors:
            cv2.fillPoly(img, [ring(hole)], 0)
    k = int(close_gap / res) | 1
    closed = cv2.morphologyEx(img, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    n, lab = cv2.connectedComponents((closed == 0).astype(np.uint8), connectivity=4)
    outside = lab[0, 0]
    rooms = []
    for i in range(1, n):
        if i == outside:
            continue
        m = (lab == i).astype(np.uint8)
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea)
        poly = Polygon([((px - pad) * res + x0, (py - pad) * res + y0) for px, py in c[:, 0]]).buffer(0)
        area = float(m.sum()) * res * res
        if area >= 1.0:
            rooms.append((poly, area))
    rooms.sort(key=lambda q: -q[1])
    return rooms


def to_mesh(geom, height):
    parts = [trimesh.creation.extrude_polygon(p, height) for p in polys_of(geom)]
    if not parts:
        raise SystemExit("Nothing to extrude.")
    m = trimesh.util.concatenate(parts)
    m.merge_vertices()
    return m


def export_step(geom, height, path):
    try:
        from build123d import Polyline, make_face, extrude, Compound, export_step as ex
    except Exception:
        return "build123d not installed; STEP skipped"
    solids = []
    for g in polys_of(geom):
        f = make_face(Polyline(*[tuple(c) for c in g.exterior.coords]))
        for h in g.interiors:
            f = f - make_face(Polyline(*[tuple(c) for c in h.coords]))
        solids.append(extrude(f, height))
    ex(Compound(solids), path)
    return None


def preview(mesh, path, title):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    fig = plt.figure(figsize=(7, 6))
    ax = fig.add_subplot(projection="3d")
    ax.add_collection3d(Poly3DCollection(mesh.vertices[mesh.faces], facecolor="#d9d4c7", edgecolor="#333", linewidth=0.15))
    lo, hi = mesh.bounds
    ax.set_xlim(lo[0], hi[0]); ax.set_ylim(lo[1], hi[1]); ax.set_zlim(0, max(hi[2], 1e-3))
    ax.set_box_aspect(np.maximum(hi - lo, 1e-3))
    ax.view_init(elev=35, azim=-60)
    ax.set_title(title, fontsize=10)
    fig.savefig(path, dpi=130, bbox_inches="tight")
    plt.close(fig)


VIEWER = """<!doctype html><html><head><meta charset="utf-8"><title>__TITLE__</title>
<style>body{margin:0;overflow:hidden;font-family:system-ui,sans-serif;background:#eef2f6}
#i{position:absolute;top:10px;left:10px;background:#1e2a3ad9;color:#fff;padding:8px 12px;border-radius:8px;font-size:13px}</style>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
</head><body><div id="i"><b>__TITLE__</b><br>__INFO__<br><span style="opacity:.7">Drag to orbit, scroll to zoom, right-drag to pan</span></div>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
const r=new THREE.WebGLRenderer({antialias:true});r.setSize(innerWidth,innerHeight);document.body.appendChild(r.domElement);
const s=new THREE.Scene();s.background=new THREE.Color(0xeef2f6);
const c=new THREE.PerspectiveCamera(45,innerWidth/innerHeight,0.01,5000);
s.add(new THREE.HemisphereLight(0xffffff,0x8899aa,1.4));const d=new THREE.DirectionalLight(0xffffff,1.6);d.position.set(1,2,1.5);s.add(d);
const ctl=new OrbitControls(c,r.domElement);
const bin=Uint8Array.from(atob("__GLB__"),ch=>ch.charCodeAt(0)).buffer;
new GLTFLoader().parse(bin,'',g=>{const o=g.scene;s.add(o);const b=new THREE.Box3().setFromObject(o),z=b.getSize(new THREE.Vector3()).length(),m=b.getCenter(new THREE.Vector3());
s.add(new THREE.GridHelper(z*2,20,0x99aabb,0xccd6e0).translateX(m.x).translateZ(m.z));
c.position.set(m.x+z*.6,m.y+z*.7,m.z+z*.8);ctl.target.copy(m);ctl.update();window.__loaded=true;});
addEventListener('resize',()=>{c.aspect=innerWidth/innerHeight;c.updateProjectionMatrix();r.setSize(innerWidth,innerHeight)});
(function a(){requestAnimationFrame(a);r.render(s,c)})();
</script></body></html>"""


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("--kind", choices=["plan", "profile"], default="plan")
    ap.add_argument("--height", type=float, default=None, help="wall height (plan, default 3.0 m) or extrusion thickness (profile, required)")
    ap.add_argument("--wall", type=float, default=0.2, help="wall thickness in m for line drawings (default 0.2)")
    ap.add_argument("--scale", type=float, default=None, help="metres per drawing unit (PDF point, SVG px, image px, or DXF unit)")
    ap.add_argument("--ref", nargs=2, type=float, metavar=("UNITS", "METRES"), help="known dimension: length in drawing units and its real length in metres")
    ap.add_argument("--layer", default=None, help="DXF layer holding walls (default: WALL/WALLS/A-WALL if present, else all)")
    ap.add_argument("--door-gap", type=float, default=1.2, help="max opening (m) closed when finding rooms")
    ap.add_argument("--brick", nargs=3, type=float, default=[0.19, 0.09, 0.09], metavar=("L", "W", "H"), help="brick size in m (10 mm mortar added)")
    ap.add_argument("--out", default="out")
    ap.add_argument("--name", default=None)
    ap.add_argument("--html", action="store_true", help="also write a single-file HTML 3D viewer")
    ap.add_argument("--no-step", action="store_true")
    ap.add_argument("--measure", action="store_true", help="print the drawing's overall width and height in drawing units (image px, PDF points, SVG px, DXF units) and exit; use it to turn one known dimension into --ref")
    a = ap.parse_args()

    if a.measure:
        ext = os.path.splitext(a.input)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"):
            import cv2
            g = cv2.imread(a.input, cv2.IMREAD_GRAYSCALE)
            if g is None:
                raise SystemExit(f"Cannot read image {a.input}")
            _, b = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            ys, xs = np.nonzero(b)
            w, h, unit = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1), "px"
            hint = "outer edge to outer edge of the ink (wall faces included). If the known dimension is the overall outside length, pass --ref <width or height> <metres>."
        else:
            if ext == ".dxf":
                lines, _, _ = read_dxf(a.input, a.layer); unit = "DXF units"
            elif ext == ".pdf":
                lines, _, _ = read_pdf(a.input); unit = "PDF points"
            elif ext == ".svg":
                lines, _, _ = read_svg(a.input); unit = "SVG px"
            else:
                raise SystemExit(f"Unsupported input type {ext}")
            pts = np.array([pt for l in lines for pt in l], dtype=float)
            w, h = (pts.max(0) - pts.min(0)).round(3).tolist()
            hint = "extent of the drawn lines (wall centrelines for single-line plans; the model adds one --wall thickness overall). If the known dimension is the overall outside length, subtract one wall thickness before using it in --ref."
        print(json.dumps({"input": os.path.basename(a.input), "width": w, "height": h, "units": unit,
                          "hint": hint}))
        return

    ext = os.path.splitext(a.input)[1].lower()
    name = a.name or os.path.splitext(os.path.basename(a.input))[0]
    os.makedirs(a.out, exist_ok=True)
    assumptions, warnings = [], []
    scale = a.scale or (a.ref[1] / a.ref[0] if a.ref else None)
    if a.kind == "profile" and a.height is None:
        raise SystemExit("profile mode needs --height (the extrusion thickness in metres).")
    height = a.height if a.height is not None else 3.0
    if a.height is None:
        assumptions.append("3.0 m wall height assumed")

    centre_len = None
    if ext in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"):
        if not scale:
            raise SystemExit("Raster input needs a scale: --scale M_PER_PX or --ref PIXELS METRES (one known dimension).")
        geom, notes = read_raster(a.input, scale)
        warnings.append("traced from a raster image: hand sketches and photos come out rough")
    else:
        if ext == ".dxf":
            lines, unit, notes = read_dxf(a.input, a.layer)
            if not scale:
                if unit:
                    scale = unit
                    notes.append(f"DXF $INSUNITS gives {unit} m per unit")
                else:
                    scale = 1.0
                    assumptions.append("DXF has no units set; drawing units assumed to be metres")
        elif ext == ".pdf":
            lines, _, notes = read_pdf(a.input)
            if not scale:
                raise SystemExit("PDF input needs a scale: --scale M_PER_POINT or --ref POINTS METRES.")
        elif ext == ".svg":
            lines, _, notes = read_svg(a.input)
            if not scale:
                raise SystemExit("SVG input needs a scale: --scale M_PER_PX or --ref PX METRES.")
        elif ext == ".dwg":
            raise SystemExit("DWG is not supported directly. Export DXF from your CAD app (AutoCAD: Save As > DXF) and run again.")
        else:
            raise SystemExit(f"Unsupported input type {ext}")
        if a.kind == "plan":
            geom, centre_len = walls_from_lines(lines, scale, a.wall)
            assumptions.append(f"{a.wall:.2f} m wall thickness assumed for drawn lines")
        else:
            geom = profile_from_lines(lines, scale)

    mesh = to_mesh(geom, height)
    lo, hi = mesh.bounds
    size = (hi - lo).tolist()

    base = os.path.join(a.out, name)
    mesh.export(f"{base}.stl")  # Z-up, metres (3D-printing convention)
    yup = mesh.copy()  # glTF and OBJ viewers expect Y-up
    yup.apply_transform(trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    yup.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
        baseColorFactor=[235, 230, 218, 255], metallicFactor=0.0, roughnessFactor=0.9))
    yup.export(f"{base}.glb")
    yup.export(f"{base}.obj", mtl_name=f"{name}.mtl")
    step_note = None if a.no_step else export_step(geom, height, f"{base}.step")
    if step_note:
        warnings.append(step_note)
    preview(mesh, f"{base}.png", f"{name} - {size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} m")

    rep = {
        "input": os.path.basename(a.input), "kind": a.kind,
        "size_m": {"x": round(size[0], 3), "y": round(size[1], 3), "z": round(size[2], 3)},
        "watertight": bool(mesh.is_watertight),
        "footprint_area_m2": round(geom.area, 6 if geom.area < 1 else 3),
        "volume_m3": round(float(mesh.volume), 9 if mesh.volume < 1 else 3),
        "files": [f"{name}.{e}" for e in ("glb", "stl", "obj", "mtl", "png")] + ([] if (a.no_step or step_note) else [f"{name}.step"]),
        "notes": notes, "assumptions": assumptions, "warnings": warnings,
        "disclaimer": "All quantities are approximate, for planning only. Not a structural design or a bill of quantities.",
    }
    if a.kind == "plan":
        rooms = rooms_of(geom, a.door_gap)
        bl, bw, bh = a.brick
        per_m3 = 1.0 / ((bl + 0.01) * (bw + 0.01) * (bh + 0.01))
        rep.update({
            "wall_centreline_length_m": round(centre_len, 2) if centre_len is not None else round(geom.area / a.wall, 2),
            "rooms": [{"id": i + 1, "area_m2": round(ar, 2),
                       "approx_dims_m": [round(v, 2) for v in (r.bounds[2] - r.bounds[0], r.bounds[3] - r.bounds[1])],
                       "centre_m": [round(r.centroid.x, 2), round(r.centroid.y, 2)]} for i, (r, ar) in enumerate(rooms)],
            "material_estimate": {
                "concrete_m3_if_cast": round(float(mesh.volume), 2),
                "bricks_count": int(round(float(mesh.volume) * per_m3, -1)),
                "brick_size_m": a.brick, "mortar_joint_m": 0.01,
            },
        })
        if centre_len is None:
            warnings.append("wall length estimated from footprint / wall thickness (raster input)")
        if max(size[:2]) > 300 or max(size[:2]) < 2:
            warnings.append(f"scale check: plan is {size[0]:.1f} x {size[1]:.1f} m, which looks off; check the scale/units")
        if rooms and max(ar for _, ar in rooms) > 2000:
            warnings.append("scale check: a room is over 2000 m2; check the scale/units")
    if not mesh.is_watertight:
        warnings.append("mesh is not watertight; check before 3D printing")

    if a.html:
        info = f"{size[0]:.2f} x {size[1]:.2f} x {size[2]:.2f} m &middot; footprint {geom.area:.2f} m&sup2;"
        html = VIEWER.replace("__TITLE__", name).replace("__INFO__", info).replace(
            "__GLB__", base64.b64encode(open(f"{base}.glb", "rb").read()).decode())
        open(f"{base}.html", "w").write(html)
        rep["files"].append(f"{name}.html")

    json.dump(rep, open(f"{base}.report.json", "w"), indent=2)
    rep["files"].append(f"{name}.report.json")
    print(json.dumps(rep, indent=2))


if __name__ == "__main__":
    main()
