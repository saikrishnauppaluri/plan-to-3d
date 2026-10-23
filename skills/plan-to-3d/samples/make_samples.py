"""Generate the sample drawings in this folder (sample_plan.dxf/.pdf/.png, sample_profile.svg).
A simple 10 m x 7 m single-storey plan: living room, two bedrooms, door gaps of 0.9-1.0 m."""
import ezdxf, cv2, numpy as np, pymupdf, os
here = os.path.dirname(os.path.abspath(__file__))
# wall centrelines in metres; gaps in the lines are door openings
W = [((0,0),(4.5,0)), ((5.5,0),(10,0)),            # front wall with 1.0 m entrance
     ((10,0),(10,7)), ((10,7),(0,7)), ((0,7),(0,0)),
     ((6,0),(6,2.0)), ((6,2.9),(6,7)),             # living / bedrooms partition, 0.9 m door
     ((6,3.5),(7.0,3.5)), ((7.9,3.5),(10,3.5))]    # bedroom 1 / bedroom 2, 0.9 m door
d = ezdxf.new(setup=True); d.header["$INSUNITS"] = 6
d.layers.add("WALLS"); m = d.modelspace()
for a, b in W: m.add_line(a, b, dxfattribs={"layer": "WALLS"})
m.add_text("LIVING", dxfattribs={"height": 0.3, "layer": "TEXT"}).set_placement((2, 3.5))
d.saveas(os.path.join(here, "sample_plan.dxf"))
# PDF at 1:100 (1 m = 28.3465 pt), lines drawn as vectors
pt = 72 / 25.4 * 10; doc = pymupdf.open(); pg = doc.new_page(width=12*pt, height=9*pt)
for (x0,y0),(x1,y1) in W: pg.draw_line((pt+x0*pt, 8*pt-y0*pt), (pt+x1*pt, 8*pt-y1*pt), width=1)
doc.save(os.path.join(here, "sample_plan.pdf"))
# PNG at 50 px per metre, walls drawn 0.2 m (10 px) thick
s = 50; img = np.full((9*s, 12*s), 255, np.uint8)
for (x0,y0),(x1,y1) in W:
    cv2.line(img, (s+int(x0*s), 8*s-int(y0*s)), (s+int(x1*s), 8*s-int(y1*s)), 0, 10)
cv2.imwrite(os.path.join(here, "sample_plan.png"), img)
# SVG profile: a ring with a square cut-out, 100 px wide
open(os.path.join(here, "sample_profile.svg"), "w").write(
 '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">'
 '<circle cx="50" cy="50" r="45" fill="black"/><rect x="30" y="30" width="40" height="40" fill="white"/></svg>')
print("samples written")
