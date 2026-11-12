"""Find the card in a photo and warp it to a canonical, upright 672x936 image (Scryfall 'large' size).

Everything downstream (marker regions, art hashing) works in this canonical frame, which is also what
makes multi-frame foil detection possible: every frame of a tilt burst lands pixel-aligned.
"""
from __future__ import annotations

import cv2
import numpy as np

W, H = 672, 936           # canonical card size (63 x 88 mm aspect)
ASPECT = H / W             # ~1.393


def load(data: bytes | np.ndarray | str) -> np.ndarray:
    if isinstance(data, np.ndarray):
        return data
    if isinstance(data, str):
        img = cv2.imread(data)
    else:
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("could not decode image")
    return img


def _order(pts: np.ndarray) -> np.ndarray:
    """Order 4 corners as (tl, tr, br, bl) of an upright portrait card, for ANY in-plane rotation.

    Corners are sorted by angle around the centroid (clockwise), then rolled so the first edge is a
    short edge (cards are portrait). Of the two remaining choices we take the one whose top edge is
    higher in the photo; the scanner resolves the last 180-degree ambiguity by reading the collector line.
    """
    pts = pts.reshape(4, 2).astype(np.float32)
    c = pts.mean(0)
    pts = pts[np.argsort(np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0]))]
    e = [np.linalg.norm(pts[(i + 1) % 4] - pts[i]) for i in range(4)]
    if e[0] + e[2] > e[1] + e[3]:
        pts = np.roll(pts, -1, axis=0)
    if pts[2][1] + pts[3][1] < pts[0][1] + pts[1][1]:
        pts = np.roll(pts, 2, axis=0)
    return pts


def _gradient(img: np.ndarray) -> np.ndarray:
    """Edge strength: max Sobel magnitude over colour channels, so colour-only edges count too."""
    b = cv2.GaussianBlur(img, (5, 5), 0).astype(np.float32)
    gx, gy = cv2.Sobel(b, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(b, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.sqrt(gx ** 2 + gy ** 2)
    return mag.max(axis=2) if mag.ndim == 3 else mag


def _side_samples(p0, p1, n=40, margin=0.12):
    t = np.linspace(margin, 1 - margin, n)[:, None]
    pts = p0 + (p1 - p0) * t
    d = (p1 - p0) / (np.linalg.norm(p1 - p0) + 1e-9)
    return pts, np.array([-d[1], d[0]])


def _probe(grad, pts, normal, radius):
    """For each sample point, the strongest gradient within +-radius along the normal, and where it is."""
    h, w = grad.shape
    offs = np.arange(-radius, radius + 1)
    xy = pts[:, None, :] + offs[None, :, None] * normal[None, None, :]
    xi = np.clip(np.round(xy[..., 0]).astype(int), 0, w - 1)
    yi = np.clip(np.round(xy[..., 1]).astype(int), 0, h - 1)
    vals = grad[yi, xi]
    k = vals.argmax(1)
    return vals[np.arange(len(pts)), k], xy[np.arange(len(pts)), k]


def edge_support(grad: np.ndarray, quad: np.ndarray, thresh: float, radius: int = 3) -> float:
    """Fraction of points along the WEAKEST side that sit on a real edge (all 4 sides must be visible)."""
    fr = []
    for i in range(4):
        pts, nrm = _side_samples(quad[i], quad[(i + 1) % 4])
        v, _ = _probe(grad, pts, nrm, radius)
        fr.append(float((v > thresh).mean()))
    return min(fr)


def tight_fit(grad: np.ndarray, quad: np.ndarray) -> float:
    """Mean edge strength exactly on the quad's sides (1px tolerance, nearly corner to corner)."""
    tot = 0.0
    for i in range(4):
        pts, nrm = _side_samples(quad[i], quad[(i + 1) % 4], n=60, margin=0.04)
        tot += float(_probe(grad, pts, nrm, 1)[0].mean())
    return tot / 4


def refine(grad: np.ndarray, quad: np.ndarray, radius: int, thresh: float | None = None) -> np.ndarray:
    """Snap each side onto the card's edge with a robust line fit, then intersect neighbouring lines.

    Along each side we look +-radius px across it and take the OUTERMOST strong edge (a card's frame line
    is always inside its outer edge and is often the stronger edge). Corners are excluded from the fit,
    so rounded card corners don't pull it inward.
    """
    quad = np.asarray(quad, np.float32)
    if thresh is None:
        thresh = max(12.0, 2.5 * float(np.median(grad)))
    h, w = grad.shape
    centre = quad.mean(0)
    lines = []
    for i in range(4):
        pts, nrm = _side_samples(quad[i], quad[(i + 1) % 4], n=60, margin=0.15)
        if np.dot(nrm, pts.mean(0) - centre) < 0:
            nrm = -nrm                                    # make the normal point outward
        offs = np.arange(-radius, radius + 1)
        xy = pts[:, None, :] + offs[None, :, None] * nrm[None, None, :]
        xi = np.clip(np.round(xy[..., 0]).astype(int), 0, w - 1)
        yi = np.clip(np.round(xy[..., 1]).astype(int), 0, h - 1)
        vals = grad[yi, xi]
        peak = np.zeros_like(vals, bool)                 # local maxima above threshold
        peak[:, 1:-1] = (vals[:, 1:-1] >= vals[:, :-2]) & (vals[:, 1:-1] >= vals[:, 2:]) & (vals[:, 1:-1] > thresh)
        # Straight-edge consensus: in each third of the side, find offsets where most samples have an
        # edge peak (a real straight edge lines up; background clutter doesn't), take the outermost such
        # offset, and keep the samples whose peaks sit on it.
        chosen = []
        for seg in np.array_split(np.arange(len(pts)), 3):
            cnt = peak[seg].sum(0).astype(float)
            cnt = np.convolve(cnt, [1, 1, 1], "same")     # tolerate +-1 px jitter / slight slope
            good = np.where(cnt >= 0.5 * len(seg))[0]
            if not len(good):
                continue
            o = good.max()
            for j in seg:
                ks = np.where(peak[j, max(0, o - 2):o + 3])[0]
                if len(ks):
                    chosen.append(xy[j, max(0, o - 2) + ks[-1]])
        if len(chosen) < 8:
            return quad
        best = np.array(chosen)
        vx, vy, x0, y0 = cv2.fitLine(best.astype(np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    out = []
    for i in range(4):
        (p, d), (q, e) = lines[i - 1], lines[i]
        a = np.array([d, -e]).T
        if abs(np.linalg.det(a)) < 1e-6:
            return quad
        t = np.linalg.solve(a, q - p)[0]
        out.append(p + t * d)
    out = np.array(out, np.float32)
    if np.max(np.linalg.norm(out - quad, axis=1)) > 3 * radius:
        return quad                                      # implausible jump: keep what we had
    return out


def _candidates(small: np.ndarray):
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    maps = []
    for ch, blur in ((gray, 5), (hsv[..., 1], 5), (gray, 9)):
        b = cv2.GaussianBlur(ch, (blur, blur), 0)
        med = float(np.median(b))
        for lo_k, hi_k in ((0.33, 1.0), (0.1, 0.4)):
            lo, hi = max(5, lo_k * med), max(15, hi_k * med + 20)
            maps.append(cv2.dilate(cv2.Canny(b, lo, hi), np.ones((3, 3), np.uint8)))
        maps.append(cv2.Canny(b, 5, 15))
    for m in maps:
        cnts, _ = cv2.findContours(m, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            if cv2.contourArea(cv2.convexHull(c)) < 0.03 * m.size:
                continue
            hull = cv2.convexHull(c)
            peri = cv2.arcLength(hull, True)
            for eps in (0.015, 0.03, 0.05):
                ap = cv2.approxPolyDP(hull, eps * peri, True)
                if len(ap) == 4:
                    yield ap.reshape(4, 2).astype(np.float32)
                    break
            yield cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)


def _shape_score(q: np.ndarray, img_area: float) -> float:
    e = [np.linalg.norm(q[(i + 1) % 4] - q[i]) for i in range(4)]
    short, long_ = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
    if short > long_:
        short, long_ = long_, short
    if short < 20:
        return 0.0
    aspect_err = abs(np.log((long_ / short) / ASPECT))
    parallel_err = abs(np.log((e[0] + 1) / (e[2] + 1))) + abs(np.log((e[1] + 1) / (e[3] + 1)))
    if aspect_err > 0.25 or parallel_err > 0.6:
        return 0.0
    area = cv2.contourArea(q) / img_area
    return (1 - aspect_err * 2.5) * (1 - parallel_err) * min(1.0, area / 0.15) ** 0.5


def _covers(outer: np.ndarray, inner: np.ndarray, inner_area: float) -> bool:
    """Does `outer` cover (almost) all of `inner`? Tolerant of sloppy corners from bounding rectangles."""
    inter, _ = cv2.intersectConvexConvex(cv2.convexHull(outer), cv2.convexHull(inner))
    return inter >= 0.92 * inner_area


def band_uniformity(img: np.ndarray, quad: np.ndarray, inset: float = 0.03, inner: np.ndarray | None = None) -> float:
    """How uniform is the strip just inside the quad (or the ring between quad and `inner`)?
    Card borders are a single colour; a rectangle of random background usually isn't. 1 = uniform."""
    mask = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, quad.astype(np.int32), 255)
    if inner is None:
        c = quad.mean(0)
        inner = c + (quad - c) * (1 - 2 * inset)
    cv2.fillConvexPoly(mask, inner.astype(np.int32), 0)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[mask > 0].astype(np.float32)
    if len(lab) < 50:
        return 0.0
    dev = np.abs(lab - np.median(lab, 0)).sum(1)
    return float((dev < 30).mean())      # share of the band within ~one colour of its median


def grow_by_border(img: np.ndarray, inner: np.ndarray) -> list[np.ndarray]:
    """Candidate outer edges grown from a frame-like quad: flood the uniform border colour just outside it.
    Finds the card edge even when the outline merges into a busy background."""
    c = inner.mean(0)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    ring = np.zeros(img.shape[:2], np.uint8)
    cv2.fillConvexPoly(ring, (c + (inner - c) * 1.06).astype(np.int32), 255)
    cv2.fillConvexPoly(ring, (c + (inner - c) * 1.01).astype(np.int32), 0)
    if ring.sum() == 0:
        return []
    col = np.median(lab[ring > 0], 0)
    search = np.zeros_like(ring)
    cv2.fillConvexPoly(search, (c + (inner - c) * 1.45).astype(np.int32), 255)
    out = []
    for tol in (18, 28, 40):
        m = ((np.abs(lab - col).sum(2) < tol) & (search > 0)).astype(np.uint8) * 255
        cv2.fillConvexPoly(m, inner.astype(np.int32), 255)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))   # cut thin leaks into background
        n, lbl = cv2.connectedComponents(m)
        k = lbl[int(c[1]), int(c[0])]
        if k == 0:
            continue
        cnts, _ = cv2.findContours((lbl == k).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        hull = cv2.convexHull(max(cnts, key=cv2.contourArea))
        for eps in (0.01, 0.02, 0.04):
            ap = cv2.approxPolyDP(hull, eps * cv2.arcLength(hull, True), True)
            if len(ap) == 4:
                out.append(_order(ap.reshape(4, 2).astype(np.float32)))
                break
        out.append(_order(cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)))
    return out


def find_card(img: np.ndarray, return_score: bool = False):
    """Locate the card at any rotation. Returns corners (tl, tr, br, bl) in image coords, or None.

    Many candidate quadrilaterals are generated from several edge maps; each is scored on card proportions
    and on whether all four sides lie on real edges; the winner is refined to sub-pixel edges.
    """
    corners, score, _ = _find(img)
    return (corners, score) if return_score else corners


def _overlap(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """(intersection / smaller area, intersection / union) of two convex quads."""
    inter, _ = cv2.intersectConvexConvex(cv2.convexHull(a.astype(np.float32)), cv2.convexHull(b.astype(np.float32)))
    aa, ab = cv2.contourArea(a.astype(np.float32)), cv2.contourArea(b.astype(np.float32))
    return inter / max(min(aa, ab), 1e-6), inter / max(aa + ab - inter, 1e-6)


# Where the picture sits on the card (x0, y0, x1, y1 as fractions): 2003+ frames, and the 1993-2003 frame.
ART_BOXES = ((0.08, 0.11, 0.92, 0.55), (0.115, 0.115, 0.89, 0.545))


def art_box_hypotheses(quad: np.ndarray) -> list[np.ndarray]:
    """Card outlines for the case that `quad` is really the card's art box.

    The art box is a landscape rectangle with nearly a card's proportions, so the detector can lock onto it
    (turned 90 degrees) when the card's own edge is weak: black border on a dark table, a sleeve, a slab.
    For both ways the picture could be turned and both frame generations, extrapolate where the whole card is.
    """
    quad = _order(quad)
    full = np.array([[[0, 0], [W, 0], [W, H], [0, H]]], np.float32)
    out = []
    for x0, y0, x1, y1 in ART_BOXES:
        src = np.array([[x0 * W, y0 * H], [x1 * W, y0 * H], [x1 * W, y1 * H], [x0 * W, y1 * H]], np.float32)
        for roll in (1, 3):
            m = cv2.getPerspectiveTransform(src, np.roll(quad, -roll, axis=0).astype(np.float32))
            out.append(cv2.perspectiveTransform(full, m)[0])
    return out


def proposals(img: np.ndarray, limit: int = 12) -> list[np.ndarray | None]:
    """Outlines worth trying, best guess first (None = no card found: use the whole photo).

    After the detector's choice come other well-scored outlines around the same spot and the art-box
    hypotheses of each. The detector cannot tell which is right from edges alone; a caller that can check
    the result (the picture recogniser) tries them when the first one matches poorly.
    """
    corners, _, others = _find(img)
    if corners is None:
        return [None]
    out = [corners] + others
    for q in [corners] + others[:1]:
        out += art_box_hypotheses(q)
    h, w = img.shape[:2]
    frame = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    keep = [out[0]]
    for q in out[1:]:
        if cv2.isContourConvex(q.astype(np.float32)) and _overlap(q, frame)[0] > 0.6:   # mostly inside the photo
            keep.append(q)
    return keep[:limit]


def _find(img: np.ndarray):
    """-> (corners or None, score, other plausible outlines around the same spot)."""
    scale = min(1.0, 900 / max(img.shape[:2]))
    small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
    grad = _gradient(small)
    thresh = max(12.0, 2.5 * float(np.median(grad)))
    img_area = small.shape[0] * small.shape[1]
    scored, seen = [], []
    for q in _candidates(small):
        q = _order(q)
        if any(np.abs(q - s).max() < 4 for s in seen):
            continue
        seen.append(q)
        shape = _shape_score(q, img_area)
        if shape <= 0:
            continue
        sup = edge_support(grad, q, thresh)
        uni = band_uniformity(small, q)
        scored.append((shape * sup * (0.3 + 0.7 * uni), sup, q))
    if not scored or max(s for s, _, _ in scored) < 0.2:
        return None, 0.0, []
    best_score, _, best = max(scored, key=lambda t: t[0])
    # The frame, art box and text box are card-shaped rectangles too, so the best-scoring quad may be one
    # of them. The card's outer edge encloses them all: keep stepping out to the nearest enclosing quad
    # whose four sides all sit on real edges, until there is none.
    while True:
        area = cv2.contourArea(best)
        for q in grow_by_border(small, best):          # border-colour hypotheses join the pool
            q = _order(refine(grad, q, radius=4, thresh=thresh))
            if _shape_score(q, img_area) > 0:
                sup = edge_support(grad, q, thresh)
                scored.append((_shape_score(q, img_area) * sup, sup, q))
        poly = best.reshape(-1, 1, 2)
        outer = [(cv2.contourArea(q), sc, q) for sc, sup, q in scored
                 if area * 1.02 < cv2.contourArea(q) < area * 1.6
                 and sup >= 0.5 and band_uniformity(small, q, inner=best) > 0.6   # the ring must be a plain border
                 and _covers(q, best, area)
                 and cv2.pointPolygonTest(poly, tuple(map(float, q.mean(0))), False) > 0]
        if not outer:
            break
        _, best_score, best = max(outer, key=lambda t: t[0])
    diag = float(np.linalg.norm(best[0] - best[2]))
    for r in (max(6, int(0.035 * diag)), 5):             # coarse snap (fixes bounding-box slop), then fine
        best = _order(refine(grad, best, radius=r, thresh=thresh))
    others = []
    for _, _, q in sorted(scored, key=lambda t: -t[0]):
        if len(others) == 3:
            break
        # only around the same spot: an outline elsewhere in the photo is a different card (or object)
        if _overlap(q, best)[0] >= 0.5 and all(_overlap(q, o)[1] < 0.75 for o in [best] + others):
            others.append(q)
    corners = best / scale
    if scale < 1:  # final snap at full resolution for crisp OCR
        corners = _order(refine(_gradient(img), corners, radius=max(3, int(2 / scale))))
    return corners, round(float(best_score), 3), [o / scale for o in others]


def track(img: np.ndarray) -> dict:
    """Cheap per-frame report for live camera guidance: where the card is and whether it's ready to capture."""
    h, w = img.shape[:2]
    corners, score = find_card(img, return_score=True)
    if corners is None:
        return {"found": False, "score": float(score), "hint": "no card found"}
    fill = cv2.contourArea(corners) / (w * h)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, corners.astype(np.int32), 255)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F)[mask > 0].var())
    # a corner on/over the frame edge means part of the card is out of view: the outline is a guess
    m = 0.006 * max(w, h)
    cut = bool(((corners[:, 0] < m) | (corners[:, 0] > w - 1 - m) | (corners[:, 1] < m) | (corners[:, 1] > h - 1 - m)).any())
    hint = ("card is cut off - move back" if cut else "move closer" if fill < 0.12
            else "too blurry - hold still" if sharp < 60 else "ready")
    return {"found": True, "score": score, "corners": (corners / [w, h]).round(4).tolist(),
            "fill": round(float(fill), 3), "sharpness": round(sharp, 1), "cut_off": cut, "hint": hint}


def warp(img: np.ndarray, corners: np.ndarray | None = None) -> np.ndarray:
    """Perspective-correct the card to W x H portrait. With no corners, treat the whole image as the card."""
    if corners is None:
        corners = find_card(img)
    if corners is None:
        h, w = img.shape[:2]
        out = img if h >= w else cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        return cv2.resize(out, (W, H), interpolation=cv2.INTER_AREA)
    corners = _order(np.asarray(corners, np.float32))   # any rotation -> upright portrait
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], np.float32)
    m = cv2.getPerspectiveTransform(corners.astype(np.float32), dst)
    return cv2.warpPerspective(img, m, (W, H), flags=cv2.INTER_CUBIC)


def rotate180(card: np.ndarray) -> np.ndarray:
    return cv2.rotate(card, cv2.ROTATE_180)


# Marker regions as fractions of the canonical card (x0, y0, x1, y1). Tuned for the 2003/2015 frames;
# they're padded generously because OCR tolerates margin far better than clipping.
REGIONS = {
    "name":       (0.06, 0.035, 0.80, 0.105),
    "type_line":  (0.06, 0.555, 0.84, 0.615),
    "set_symbol": (0.82, 0.555, 0.95, 0.615),
    "art":        (0.08, 0.110, 0.92, 0.550),
    "collector":  (0.025, 0.920, 0.62, 0.990),
}


def crop(card: np.ndarray, region: str | tuple) -> np.ndarray:
    x0, y0, x1, y1 = REGIONS[region] if isinstance(region, str) else region
    h, w = card.shape[:2]
    return card[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]


def border_band(card: np.ndarray, frac: float = 0.022) -> np.ndarray:
    """Pixels of the outer border band (excluding the rounded corners) as an (N, 3) BGR array."""
    h, w = card.shape[:2]
    b = max(2, int(frac * w))
    cy, cx = int(0.06 * h), int(0.06 * w)
    parts = [card[:b, cx:w - cx], card[h - b:, cx:w - cx], card[cy:h - cy, :b], card[cy:h - cy, w - b:]]
    return np.concatenate([p.reshape(-1, 3) for p in parts])
