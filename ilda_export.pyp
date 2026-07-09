# -*- coding: utf-8 -*-
"""
ilda_export.pyp -- Cinema 4D command plugin: export spline/mesh animations to ILDA.

Works on Cinema 4D R21 (Python 2.7) through 2025 (Python 3). Adds a menu command,
"Export ILDA (.ild)...". Running it shows an options dialog (what to export + how to
project), then a Save dialog, then writes one ILDA frame per animation frame.

Export options (chosen at export time in the dialog):
  * Splines           -- sample every spline / spline generator (on by default).
  * Mesh objects      -- extract edges from polygon objects (cubes, spheres, editable
                         meshes, generator output). Off by default.
      - All edges       every mesh edge as a line segment.
      - Facing edges    only edges of front-facing polygons (back edges culled).
      - Silhouette      only the outline edges for the chosen viewpoint (cleaner).
  * Projection        -- Front (XY orthographic) or Active camera (perspective).

Folder layout (all together in one plugins subfolder):
    <C4D plugins>/ilda_export/
        ilda_export.pyp
        ilda_format.py
        laser_build.py

Invoke:  Extensions menu -> "Export ILDA (.ild)...",  or Shift+C -> "Export ILDA".

Version-sensitive API calls are marked ##VERIFY## and all have fallbacks.
"""

from __future__ import division, print_function

import os
import sys
import math
import traceback

import c4d
from c4d import plugins, utils

__version__ = "1.0.0"

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

print("[ILDA] c4d-ilda-export v%s loading from: %s" % (__version__, _HERE))

import ilda_format          # noqa: E402
import laser_build          # noqa: E402


# ---------------------------------------------------------------------------
PLUGIN_ID = 1069168   # personal PluginCafe ID (edmonkey / ILDASaver)
# ---------------------------------------------------------------------------

CONFIG = {
    # Defaults for the export dialog:
    "projection": "xy",          # "xy" (front ortho) or "camera" (active camera)
    "export_splines": True,
    "export_meshes": False,
    "mesh_edges": "all",         # "all", "facing" or "silhouette"
    # Front-view facing direction is ambiguous in an orthographic export. C4D is
    # left-handed (+Z into the screen), so "toward viewer" defaults to -Z. If the
    # "facing" mode shows the BACK of your object instead of the front, flip this.
    "flip_facing": False,

    # Sampling / output tuning (not shown in the dialog; edit here):
    "invert_y": True,            # laser Y points up; flip if the image is upside-down
    "samples_per_segment": 64,   # interpolated points per spline segment
    "close_closed_splines": True,
    "blank_dwell": 3,            # blanked travel points inserted before each stroke
    "on_dwell": 2,               # repeats of a stroke's first lit point (galvo settle)
    "margin": 0.06,              # fraction of the device range kept as a safe border
    "true_color_3d": False,      # False -> ILDA format 5 (2D); True -> format 4 (3D)
    "frame_step": 1,             # export every Nth frame
    "use_preview_range": False,  # True -> loop/preview range, else full document range
    "max_points_per_frame": 16000,

    "default_color": (255, 255, 255),
    "name": "C4D",
    "company": "ILDAEXP",
}

_BUILDFLAGS = getattr(c4d, "BUILDFLAGS_INTERNALRENDERER",
                      getattr(c4d, "BUILDFLAGS_0", 0))


# ---------------------------------------------------------------------------
# Scene traversal helpers
# ---------------------------------------------------------------------------

def _iter_objects(op):
    while op:
        yield op
        for child in _iter_objects(op.GetDown()):
            yield child
        op = op.GetNext()


def _object_color(op):
    try:
        if op[c4d.ID_BASEOBJECT_USECOLOR] == c4d.ID_BASEOBJECT_USECOLOR_ON:
            col = op[c4d.ID_BASEOBJECT_COLOR]
            return (int(col.x * 255), int(col.y * 255), int(col.z * 255))
    except Exception:
        pass
    return CONFIG["default_color"]


# ---------------------------------------------------------------------------
# Spline extraction
# ---------------------------------------------------------------------------

def _segment_closed(spline, seg_index, seg_count):
    try:
        if seg_count > 1:
            seg = spline.GetSegment(seg_index)
            return bool(seg["closed"])
        return bool(spline.IsClosed())
    except Exception:
        try:
            return bool(spline.IsClosed())
        except Exception:
            return False


def _sample_spline(op):
    spline = None
    try:
        spline = op.GetRealSpline()            ##VERIFY## BaseObject.GetRealSpline
    except Exception:
        spline = None
    if spline is None:
        return None

    mg = op.GetMg()
    n = max(2, int(CONFIG["samples_per_segment"]))

    sh = None
    seg_count = 1
    try:
        sh = c4d.utils.SplineHelp()
        try:
            sh.InitSplineWith(spline, c4d.SPLINEHELPFLAGS_RETAINLINEOBJECT)  ##VERIFY##
        except Exception:
            sh.InitSpline(spline)                                            ##VERIFY##
        seg_count = max(1, sh.GetSegmentCount())
    except Exception:
        sh = None

    polylines = []
    if sh is not None:
        try:
            for s in range(seg_count):
                closed = _segment_closed(spline, s, seg_count)
                count = n if closed else n + 1
                poly = []
                for i in range(count):
                    off = float(i) / float(n)
                    if off > 1.0:
                        off = 1.0
                    poly.append(mg * sh.GetPosition(off, s))
                if closed and CONFIG["close_closed_splines"] and poly:
                    poly.append(poly[0])
                if len(poly) >= 2:
                    polylines.append(poly)
            if polylines:
                return polylines
        except Exception:
            polylines = []

    try:
        allpts = spline.GetAllPoints()
        seg_count = max(1, spline.GetSegmentCount())
        idx = 0
        for s in range(seg_count):
            try:
                seg = spline.GetSegment(s)
                cnt = seg["cnt"]
                closed = bool(seg["closed"])
            except Exception:
                cnt = len(allpts)
                closed = _segment_closed(spline, s, seg_count)
            poly = [mg * allpts[idx + k] for k in range(cnt)]
            idx += cnt
            if closed and CONFIG["close_closed_splines"] and poly:
                poly.append(poly[0])
            if len(poly) >= 2:
                polylines.append(poly)
    except Exception:
        return None
    return polylines or None


# ---------------------------------------------------------------------------
# Mesh (polygon) extraction -> edges
# ---------------------------------------------------------------------------

def _mesh_polys_world(op):
    """Return [(PolygonObject, world_matrix), ...] for op's final mesh, or []."""
    out = []
    try:
        if op.CheckType(c4d.Opolygon):
            out.append((op, op.GetMg()))
            return out
        cache = op.GetDeformCache()
        if cache is None:
            cache = op.GetCache()
        if cache is None:
            return out

        def walk(o, parent_mg):
            while o:
                try:
                    mg = parent_mg * o.GetMl()
                except Exception:
                    mg = parent_mg
                deform = o.GetDeformCache()
                if deform is not None:
                    walk(deform, mg)
                else:
                    sub = o.GetCache()
                    if sub is not None:
                        walk(sub, mg)
                    elif o.CheckType(c4d.Opolygon):
                        out.append((o, mg))
                o = o.GetNext()

        walk(cache, op.GetMg())
    except Exception:
        return out
    return out


def _all_edges(pobj):
    edges = set()
    for f in range(pobj.GetPolygonCount()):
        cp = pobj.GetPolygon(f)
        idx = (cp.a, cp.b, cp.c) if cp.c == cp.d else (cp.a, cp.b, cp.c, cp.d)
        m = len(idx)
        for k in range(m):
            i, j = idx[k], idx[(k + 1) % m]
            if i != j:
                edges.add((i, j) if i < j else (j, i))
    return edges


def _silhouette_edges(pobj, pts, view_dir):
    edge_faces = {}
    face_front = []
    for f in range(pobj.GetPolygonCount()):
        cp = pobj.GetPolygon(f)
        tri = (cp.c == cp.d)
        idx = (cp.a, cp.b, cp.c) if tri else (cp.a, cp.b, cp.c, cp.d)
        try:
            a, b, c = pts[cp.a], pts[cp.b], pts[cp.c]
            normal = (b - a) % (c - a)          # cross product
            if tri:
                cen = (pts[cp.a] + pts[cp.b] + pts[cp.c]) * (1.0 / 3.0)
            else:
                cen = (pts[cp.a] + pts[cp.b] + pts[cp.c] + pts[cp.d]) * 0.25
            face_front.append((normal * view_dir(cen)) > 0)   # dot product
        except Exception:
            face_front.append(True)
        m = len(idx)
        for k in range(m):
            i, j = idx[k], idx[(k + 1) % m]
            if i == j:
                continue
            key = (i, j) if i < j else (j, i)
            edge_faces.setdefault(key, []).append(f)

    sil = []
    for key, faces in edge_faces.items():
        if len(faces) == 1:
            sil.append(key)                      # boundary edge
        else:
            f0 = face_front[faces[0]]
            if any(face_front[fx] != f0 for fx in faces[1:]):
                sil.append(key)
    return sil


def _facing_edges(pobj, pts, view_dir):
    """Edges belonging to at least one front-facing polygon (back edges culled).

    Superset of the silhouette: keeps interior edges of visible faces too, so you
    get the front wireframe without the far-side edges obscured by the object.
    """
    edge_faces = {}
    face_front = []
    for f in range(pobj.GetPolygonCount()):
        cp = pobj.GetPolygon(f)
        tri = (cp.c == cp.d)
        idx = (cp.a, cp.b, cp.c) if tri else (cp.a, cp.b, cp.c, cp.d)
        try:
            a, b, c = pts[cp.a], pts[cp.b], pts[cp.c]
            normal = (b - a) % (c - a)
            if tri:
                cen = (pts[cp.a] + pts[cp.b] + pts[cp.c]) * (1.0 / 3.0)
            else:
                cen = (pts[cp.a] + pts[cp.b] + pts[cp.c] + pts[cp.d]) * 0.25
            face_front.append((normal * view_dir(cen)) > 0)
        except Exception:
            face_front.append(True)
        m = len(idx)
        for k in range(m):
            i, j = idx[k], idx[(k + 1) % m]
            if i == j:
                continue
            key = (i, j) if i < j else (j, i)
            edge_faces.setdefault(key, []).append(f)

    out = []
    for key, faces in edge_faces.items():
        if any(face_front[fx] for fx in faces):
            out.append(key)
    return out


def _sample_mesh(op, edge_mode, view_dir):
    chunks = _mesh_polys_world(op)
    if not chunks:
        return None
    polylines = []
    for pobj, mg in chunks:
        try:
            if pobj.GetPolygonCount() == 0:
                continue
            pts = [mg * p for p in pobj.GetAllPoints()]
        except Exception:
            continue
        if edge_mode == "silhouette":
            edges = _silhouette_edges(pobj, pts, view_dir)
        elif edge_mode == "facing":
            edges = _facing_edges(pobj, pts, view_dir)
        else:
            edges = _all_edges(pobj)
        npts = len(pts)
        for (i, j) in edges:
            if 0 <= i < npts and 0 <= j < npts:
                polylines.append([pts[i], pts[j]])
    return polylines or None


# ---------------------------------------------------------------------------
# Projection: returns (project_fn, view_dir_fn)
# ---------------------------------------------------------------------------

def _make_projection(doc, settings):
    if settings["projection"] == "camera":
        cam = None
        try:
            bd = doc.GetActiveBaseDraw()
            cam = bd.GetSceneCamera(doc) if bd else None
        except Exception:
            cam = None
        if cam is None:
            for op in _iter_objects(doc.GetFirstObject()):
                if op.IsInstanceOf(c4d.Ocamera):
                    cam = op
                    break
        if cam is not None:
            def project(p):
                view = ~cam.GetMg()
                pc = view * p
                if pc.z <= 1e-6:
                    return None
                try:
                    fovh = cam[c4d.CAMERAOBJECT_FOV]            ##VERIFY##
                    fovv = cam[c4d.CAMERAOBJECT_FOV_VERTICAL]   ##VERIFY##
                except Exception:
                    fovh = math.radians(53.0)
                    fovv = math.radians(31.0)
                nx = (pc.x / pc.z) / math.tan(fovh * 0.5)
                ny = (pc.y / pc.z) / math.tan(fovv * 0.5)
                return (nx, ny, 0.0)

            def view_dir(centroid):
                return cam.GetMg().off - centroid   # fresh each frame

            return project, view_dir
        print("[ILDA] No scene camera found; using XY projection.")

    def project(p):
        return (p.x, p.y, p.z)

    # C4D is left-handed (+Z into the screen), so "toward viewer" is -Z. flip_facing
    # inverts this if the front view comes out back-to-front. (Only affects the
    # "facing" mode; "silhouette" is sign-independent.)
    zdir = 1.0 if CONFIG["flip_facing"] else -1.0

    def view_dir(centroid):
        return c4d.Vector(0, 0, zdir)

    return project, view_dir


# ---------------------------------------------------------------------------
# Frame range + extraction driver
# ---------------------------------------------------------------------------

def _frame_range(doc):
    fps = doc.GetFps()
    if CONFIG["use_preview_range"]:
        tmin, tmax = doc.GetLoopMinTime(), doc.GetLoopMaxTime()
    else:
        tmin, tmax = doc.GetMinTime(), doc.GetMaxTime()
    return tmin.GetFrame(fps), tmax.GetFrame(fps), fps


def _extract_clip(doc, settings):
    f0, f1, fps = _frame_range(doc)
    step = max(1, int(CONFIG["frame_step"]))
    original = doc.GetTime()
    project, view_dir = _make_projection(doc, settings)
    edge_mode = settings["mesh_edges"]
    do_splines = settings["export_splines"]
    do_meshes = settings["export_meshes"]
    budget = int(CONFIG["max_points_per_frame"])

    clip = []
    try:
        total = ((f1 - f0) // step) + 1
        idx = 0
        for frame in range(f0, f1 + 1, step):
            doc.SetTime(c4d.BaseTime(frame, fps))
            doc.ExecutePasses(None, True, True, True, _BUILDFLAGS)

            frame_polys = []
            used = 0
            for op in _iter_objects(doc.GetFirstObject()):
                try:
                    if op.GetEditorMode() == c4d.MODE_OFF:
                        continue
                except Exception:
                    pass

                polys = None
                if do_splines:
                    polys = _sample_spline(op)
                if not polys and do_meshes:
                    polys = _sample_mesh(op, edge_mode, view_dir)
                if not polys:
                    continue

                rgb = _object_color(op)
                for poly in polys:
                    verts = []
                    for wp in poly:
                        pr = project(wp)
                        if pr is None:
                            continue
                        verts.append((pr[0], pr[1], pr[2], rgb))
                    if len(verts) >= 2:
                        used += len(verts)
                        frame_polys.append(verts)
                if used > budget:
                    print("[ILDA] Frame %d over point budget (%d); rest skipped."
                          % (frame, budget))
                    break
            clip.append(frame_polys)

            idx += 1
            c4d.StatusSetText("ILDA export %d/%d" % (idx, total))
            c4d.StatusSetBar(int(100.0 * idx / max(1, total)))
    finally:
        doc.SetTime(original)
        doc.ExecutePasses(None, True, True, True, _BUILDFLAGS)
        c4d.StatusClear()
        c4d.EventAdd()
    return clip


def _export_to(path, doc, settings):
    clip = _extract_clip(doc, settings)
    opts = laser_build.BuildOptions(
        invert_y=CONFIG["invert_y"],
        margin=CONFIG["margin"],
        blank_dwell=CONFIG["blank_dwell"],
        on_dwell=CONFIG["on_dwell"],
        true_color_3d=CONFIG["true_color_3d"],
    )
    frames = laser_build.build_clip(clip, opts)
    return ilda_format.save(
        frames, path,
        true_color_3d=CONFIG["true_color_3d"],
        name=CONFIG["name"], company=CONFIG["company"])


# ---------------------------------------------------------------------------
# Options dialog
# ---------------------------------------------------------------------------

_G_SPLINES = 2001
_G_MESHES = 2002
_G_EDGEMODE = 2003
_G_PROJ = 2004


class ILDAOptionsDialog(c4d.gui.GeDialog):

    def __init__(self):
        try:
            super(ILDAOptionsDialog, self).__init__()
        except Exception:
            pass
        self.result = None

    def CreateLayout(self):
        self.SetTitle("ILDA Export Options")
        self.GroupBegin(1000, c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT, 1, 0, "")
        self.GroupBorderSpace(12, 12, 12, 12)

        self.AddCheckbox(_G_SPLINES, c4d.BFH_LEFT, 0, 0, "Export splines")
        self.AddCheckbox(_G_MESHES, c4d.BFH_LEFT, 0, 0,
                         "Export mesh objects (extract edges)")

        self.GroupBegin(1001, c4d.BFH_SCALEFIT, 2, 0, "")
        self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, "Mesh edges:", 0)
        self.AddComboBox(_G_EDGEMODE, c4d.BFH_SCALEFIT, 0, 0)
        self.AddChild(_G_EDGEMODE, 0, "All edges")
        self.AddChild(_G_EDGEMODE, 1, "Facing edges (front only)")
        self.AddChild(_G_EDGEMODE, 2, "Silhouette edges")
        self.AddStaticText(0, c4d.BFH_LEFT, 0, 0, "Projection:", 0)
        self.AddComboBox(_G_PROJ, c4d.BFH_SCALEFIT, 0, 0)
        self.AddChild(_G_PROJ, 0, "Front (XY orthographic)")
        self.AddChild(_G_PROJ, 1, "Active camera (perspective)")
        self.GroupEnd()

        self.GroupEnd()
        self.AddDlgGroup(c4d.DLG_OK | c4d.DLG_CANCEL)
        return True

    def InitValues(self):
        self.SetBool(_G_SPLINES, CONFIG["export_splines"])
        self.SetBool(_G_MESHES, CONFIG["export_meshes"])
        self.SetInt32(_G_EDGEMODE,
                      {"all": 0, "facing": 1, "silhouette": 2}.get(
                          CONFIG["mesh_edges"], 0))
        self.SetInt32(_G_PROJ, 1 if CONFIG["projection"] == "camera" else 0)
        return True

    def Command(self, cid, msg):
        if cid == c4d.DLG_OK:
            self.result = {
                "export_splines": self.GetBool(_G_SPLINES),
                "export_meshes": self.GetBool(_G_MESHES),
                "mesh_edges": {0: "all", 1: "facing", 2: "silhouette"}.get(
                    self.GetInt32(_G_EDGEMODE), "all"),
                "projection": "camera" if self.GetInt32(_G_PROJ) == 1 else "xy",
            }
            self.Close()
        elif cid == c4d.DLG_CANCEL:
            self.result = None
            self.Close()
        return True


# ---------------------------------------------------------------------------
# Command plugin
# ---------------------------------------------------------------------------

class ILDAExportCommand(plugins.CommandData):

    def Execute(self, doc):
        dlg = ILDAOptionsDialog()
        dlg.Open(c4d.DLG_TYPE_MODAL, pluginid=PLUGIN_ID, defaultw=360, defaulth=0)
        settings = dlg.result
        if settings is None:
            return True  # cancelled

        if not settings["export_splines"] and not settings["export_meshes"]:
            c4d.gui.MessageDialog("Nothing selected to export.\n\n"
                                  "Enable splines and/or mesh objects.")
            return True

        path = c4d.storage.SaveDialog(
            type=c4d.FILESELECTTYPE_ANYTHING,
            title="Export animation to ILDA (.ild)",
            force_suffix="ild")
        if not path:
            return True

        try:
            written = _export_to(path, doc, settings)
            what = []
            if settings["export_splines"]:
                what.append("splines")
            if settings["export_meshes"]:
                what.append("mesh %s edges" % settings["mesh_edges"])
            msg = ("ILDA export complete.\n\n%d frame(s) written to:\n%s\n\n"
                   "Exported: %s\nProjection: %s"
                   % (written, path, ", ".join(what), settings["projection"]))
            print("[ILDA] %s" % msg.replace("\n", " "))
            c4d.gui.MessageDialog(msg)
        except Exception as exc:
            traceback.print_exc()
            c4d.gui.MessageDialog("ILDA export failed:\n%s" % exc)
        return True


_REGISTERED = False


def _register():
    global _REGISTERED
    if _REGISTERED:
        return
    try:
        ok = plugins.RegisterCommandPlugin(
            PLUGIN_ID, "Export ILDA (.ild)...", 0, None,
            "Export spline/mesh animation to ILDA laser format",
            ILDAExportCommand())
    except Exception as exc:
        print("[ILDA] registration error: %s" % exc)
        return
    if ok is False:
        print("[ILDA] registration FAILED (RegisterCommandPlugin returned False)")
    else:
        _REGISTERED = True
        print("[ILDA] v%s command registered (id %d) -- Extensions menu or Shift+C"
              % (__version__, PLUGIN_ID))


if __name__ == "__main__":
    _register()
