"""Shared training code for the card embedder: dataset, GPU augmentations that mimic phone photos, model, eval.

Needs the training venv (torch, timm, opencv). See docs/scanner-model-plan.md.
"""
from __future__ import annotations

import hashlib
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mtgstack.scanner.modeldata import EMBED_DIM, INPUT_H, INPUT_W, MEAN, STD, image_path, read_manifest, read_placeholders  # noqa: E402,E501

# Images are loaded a little larger than the network input so the perspective warp has detail to sample from.
LOAD_W, LOAD_H = 256, 356


# ---- data ---------------------------------------------------------------------------------------------------
def load_rows(data_dir: str | Path, limit: int = 0) -> list[dict]:
    """Manifest rows whose image is on disk."""
    data_dir = Path(data_dir).expanduser()
    skip = read_placeholders(data_dir)
    rows = [r for r in read_manifest(data_dir / "manifest.jsonl")
            if r["key"] not in skip and image_path(data_dir, r["key"]).exists()]
    return rows[:limit] if limit else rows


def is_holdout(group: str, percent: int = 3) -> bool:
    """Stable split by illustration: held-out art is never trained on, only indexed and queried at eval time
    (which is what happens to every new set after the model ships)."""
    return int(hashlib.sha1(group.encode()).hexdigest()[:8], 16) % 100 < percent


def load_image(path: str | Path) -> np.ndarray | None:
    """Card image -> RGB uint8 (LOAD_H, LOAD_W, 3)."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        return None
    img = cv2.resize(img, (LOAD_W, LOAD_H), interpolation=cv2.INTER_AREA)
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


class CardDataset(torch.utils.data.Dataset):
    """Returns (clean image, photo-source image, label, row index). The photo source is sometimes re-compressed
    as a low-quality JPEG here on the CPU; everything else is augmented on the GPU in `photo_augment`."""

    def __init__(self, rows: list[dict], data_dir: str | Path, labels: list[int], jpeg_prob: float = 0.3):
        self.rows, self.data_dir, self.labels, self.jpeg_prob = rows, Path(data_dir).expanduser(), labels, jpeg_prob

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        img = load_image(image_path(self.data_dir, self.rows[i]["key"]))
        if img is None:
            img = np.zeros((LOAD_H, LOAD_W, 3), np.uint8)
        src = img
        if self.jpeg_prob and np.random.rand() < self.jpeg_prob:
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, int(np.random.randint(20, 75))])
            if ok:
                src = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return torch.from_numpy(img), torch.from_numpy(src), self.labels[i], i


class SetBatchSampler(torch.utils.data.Sampler):
    """Batches built from same-set chunks, so most in-batch negatives are hard ones: same frame, same set symbol,
    same collector-line style, often the same artist. Chunks are shuffled every epoch."""

    def __init__(self, rows: list[dict], batch_size: int, chunk: int = 64, seed: int = 0):
        self.batch_size, self.chunk, self.rng = batch_size, chunk, np.random.default_rng(seed)
        by_set: dict[str, list[int]] = {}
        for i, r in enumerate(rows):
            by_set.setdefault(r["set"], []).append(i)
        self.sets = [np.array(v) for v in by_set.values()]
        self.n = len(rows)

    def __len__(self):
        return self.n // self.batch_size

    def __iter__(self):
        chunks = []
        for idx in self.sets:
            idx = self.rng.permutation(idx)
            chunks += [idx[i:i + self.chunk] for i in range(0, len(idx), self.chunk)]
        order = np.concatenate([chunks[i] for i in self.rng.permutation(len(chunks))])
        for i in range(0, len(order) - self.batch_size + 1, self.batch_size):
            yield order[i:i + self.batch_size].tolist()


# ---- augmentation (GPU, batched) ---------------------------------------------------------------------------
def to_input(x_uint8: torch.Tensor) -> torch.Tensor:
    """(B, H, W, 3) uint8 -> clean network input (B, 3, INPUT_H, INPUT_W) float in 0..1."""
    x = x_uint8.permute(0, 3, 1, 2).float() / 255.0
    return F.interpolate(x, size=(INPUT_H, INPUT_W), mode="bilinear", antialias=True, align_corners=False)


_corner_cache: dict = {}


def _corner_alpha(h: int, w: int, device) -> torch.Tensor:
    """1 inside the card's rounded corners (radius ~3 mm of 63 mm), 0 outside. Scryfall images fill that area with
    a flat colour; in a photo the table shows through."""
    key = (h, w, str(device))
    if key not in _corner_cache:
        r = 0.048 * w
        yy, xx = torch.meshgrid(torch.arange(h, device=device) + 0.5, torch.arange(w, device=device) + 0.5, indexing="ij")
        dx = (r - torch.minimum(xx, w - xx)).clamp(min=0)
        dy = (r - torch.minimum(yy, h - yy)).clamp(min=0)
        _corner_cache[key] = (r + 0.5 - torch.sqrt(dx * dx + dy * dy)).clamp(0, 1)[None, None]
    return _corner_cache[key]


def _homography(src: torch.Tensor, dst: torch.Tensor) -> torch.Tensor:
    """Batched 3x3 matrices mapping src (B,4,2) -> dst (B,4,2)."""
    x, y, u, v = src[..., 0], src[..., 1], dst[..., 0], dst[..., 1]
    z, o = torch.zeros_like(x), torch.ones_like(x)
    a = torch.cat([torch.stack([x, y, o, z, z, z, -u * x, -u * y], -1),
                   torch.stack([z, z, z, x, y, o, -v * x, -v * y], -1)], 1)
    h = torch.linalg.solve(a.double(), torch.cat([u, v], 1).double().unsqueeze(-1)).squeeze(-1).float()
    return torch.cat([h, torch.ones_like(h[:, :1])], 1).view(-1, 3, 3)


def _blur(x: torch.Tensor, kernels: torch.Tensor) -> torch.Tensor:
    """Per-sample kernels (B, k, k) applied to (B, C, H, W)."""
    b, c, h, w = x.shape
    k = kernels.shape[-1]
    wgt = kernels[:, None].repeat_interleave(c, 0)
    out = F.conv2d(F.pad(x.reshape(1, b * c, h, w), (k // 2,) * 4, mode="replicate"), wgt, groups=b * c)
    return out.view(b, c, h, w)


def photo_augment(x_uint8: torch.Tensor, g: torch.Generator | None = None, strength: float = 1.0,
                  rotate180: float = 0.5) -> torch.Tensor:
    """Scryfall scan -> something like a rectified phone photo of that card. (B, H, W, 3) uint8 on the GPU ->
    (B, 3, INPUT_H, INPUT_W) float 0..1.

    Lighting on the card (glare, foil rainbow, sleeve sheen, shadow) is applied first, then the residual error
    of detect+rectify (corner jitter, zoom, 180 degree turns, table showing at the edges), then the camera
    (blur, low resolution, white balance, exposure, noise).
    """
    dev = x_uint8.device
    b, h, w, _ = x_uint8.shape
    s = strength

    def U(*shape, lo=0.0, hi=1.0):
        return torch.rand(*shape, device=dev, generator=g) * (hi - lo) + lo

    def N(*shape):
        return torch.randn(*shape, device=dev, generator=g)

    def P(p):
        return (U(b, 1, 1, 1) < p * min(s, 1.0)).float()

    x = x_uint8.permute(0, 3, 1, 2).float() / 255.0
    yy, xx = torch.meshgrid(torch.linspace(-1, 1, h, device=dev), torch.linspace(-1, 1, w, device=dev), indexing="ij")
    yy, xx = yy[None, None] * (h / w), xx[None, None]            # aspect-true coordinates

    # -- light on the card ------------------------------------------------------------------------------
    # foil: rainbow bands whose hue varies across the card, strongest in a broad patch
    ang = U(b, 1, 1, 1, hi=math.pi)
    freq = U(b, 1, 1, 1, lo=1.5, hi=7.0)
    t = (xx * torch.cos(ang) + yy * torch.sin(ang)) * freq + U(b, 1, 1, 1, hi=6.28)
    rainbow = torch.cat([0.5 + 0.5 * torch.cos(t + k) for k in (0.0, 2.094, 4.189)], 1)
    cx, cy, rad = U(b, 1, 1, 1, lo=-1, hi=1), U(b, 1, 1, 1, lo=-1.4, hi=1.4), U(b, 1, 1, 1, lo=0.5, hi=1.6)
    patch = torch.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * rad ** 2))
    a = P(0.22) * U(b, 1, 1, 1, lo=0.08, hi=0.45) * s * patch
    x = x * (1 - a) + (x + rainbow * (1 - x)) * a * 0.6 + rainbow * a * 0.4
    # glare: one or two bright elliptical blobs (screen blend)
    for p in (0.35, 0.12):
        cx, cy = U(b, 1, 1, 1, lo=-1, hi=1), U(b, 1, 1, 1, lo=-1.4, hi=1.4)
        rx, ry = U(b, 1, 1, 1, lo=0.12, hi=0.7), U(b, 1, 1, 1, lo=0.12, hi=0.7)
        gl = P(p) * U(b, 1, 1, 1, lo=0.3, hi=0.95) * s * torch.exp(-(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2) / 2)
        x = x + gl * (1 - x)
    # sleeve sheen: a soft bright streak along a random line, plus a little haze
    ang = U(b, 1, 1, 1, hi=math.pi)
    d = xx * torch.cos(ang) + yy * torch.sin(ang) - U(b, 1, 1, 1, lo=-1, hi=1)
    sheen = P(0.2) * U(b, 1, 1, 1, lo=0.1, hi=0.4) * s * torch.exp(-(d / U(b, 1, 1, 1, lo=0.08, hi=0.4)) ** 2 / 2)
    x = x + sheen * (1 - x)
    haze = P(0.15) * U(b, 1, 1, 1, lo=0.03, hi=0.15) * s
    x = x * (1 - haze) + haze * 0.75
    # shadow: one side of a soft edge is darker (a hand or the phone over the card)
    ang = U(b, 1, 1, 1, hi=2 * math.pi)
    d = xx * torch.cos(ang) + yy * torch.sin(ang) - U(b, 1, 1, 1, lo=-0.8, hi=0.8)
    shade = torch.sigmoid(d / U(b, 1, 1, 1, lo=0.03, hi=0.5))
    x = x * (1 - P(0.3) * U(b, 1, 1, 1, lo=0.2, hi=0.6) * s * shade)

    # -- detect + rectify error ---------------------------------------------------------------------------
    corners = torch.tensor([[-1.0, -1.0], [1.0, -1.0], [1.0, 1.0], [-1.0, 1.0]], device=dev).expand(b, 4, 2)
    zoom = U(b, 1, 1, lo=0.90, hi=1.06)                          # <1: table visible around the card; >1: cropped in
    big = (U(b, 1, 1) < 0.15).float()
    jitter = (U(b, 4, 2, lo=-1, hi=1) * (0.045 + 0.06 * big) + U(b, 1, 2, lo=-1, hi=1) * 0.03) * s
    src_pts = corners / zoom + jitter
    flip = torch.where(U(b, 1, 1) < rotate180, -1.0, 1.0)
    hm = _homography(corners, src_pts * flip)
    gy, gx = torch.meshgrid(torch.linspace(-1, 1, INPUT_H, device=dev), torch.linspace(-1, 1, INPUT_W, device=dev),
                            indexing="ij")
    pts = torch.stack([gx, gy, torch.ones_like(gx)], -1).view(1, -1, 3) @ hm.transpose(1, 2)
    grid = (pts[..., :2] / pts[..., 2:].clamp(min=1e-6)).view(b, INPUT_H, INPUT_W, 2)
    rgba = torch.cat([x, _corner_alpha(h, w, dev).expand(b, 1, h, w)], 1)
    rgba = F.grid_sample(rgba, grid, mode="bilinear", padding_mode="zeros", align_corners=True)
    x, alpha = rgba[:, :3], rgba[:, 3:]
    # table: a random colour with low-frequency texture
    bg = U(b, 3, 1, 1) * U(b, 1, 1, 1, lo=0.2, hi=1.0)
    tex = F.interpolate(N(b, 3, 6, 5) * 0.08, size=(INPUT_H, INPUT_W), mode="bilinear", align_corners=False)
    x = x + (bg + tex).clamp(0, 1) * (1 - alpha)

    # -- camera --------------------------------------------------------------------------------------------
    # defocus + motion blur as one per-sample kernel
    k = 11
    ax = torch.arange(k, device=dev) - k // 2
    ky, kx = torch.meshgrid(ax.float(), ax.float(), indexing="ij")
    sig = (0.3 + (U(b, 1, 1) < 0.55 * s).float() * U(b, 1, 1, hi=1.8) * s)
    gauss = torch.exp(-(kx ** 2 + ky ** 2)[None] / (2 * sig ** 2))
    ang = U(b, 1, 1, hi=math.pi)
    length = (U(b, 1, 1) < 0.25 * s).float() * U(b, 1, 1, lo=1.5, hi=5.0) * s
    along = kx[None] * torch.cos(ang) + ky[None] * torch.sin(ang)
    across = -kx[None] * torch.sin(ang) + ky[None] * torch.cos(ang)
    motion = torch.exp(-(across ** 2) / 0.2) * (along.abs() <= length + 0.5).float()
    kern = gauss / gauss.sum((1, 2), keepdim=True)
    mk = motion / motion.sum((1, 2), keepdim=True)
    # convolve the two small kernels so a frame can have both
    kern = F.conv2d(kern[None], mk[:, None], padding=k // 2, groups=b)[0]
    x = _blur(x, kern / kern.sum((1, 2), keepdim=True))
    # low resolution: card far from the camera
    f = float(U(1, lo=0.45, hi=0.85))
    low = F.interpolate(F.interpolate(x, scale_factor=f, mode="bilinear", antialias=True, align_corners=False),
                        size=(INPUT_H, INPUT_W), mode="bilinear", align_corners=False)
    m = P(0.25)
    x = x * (1 - m) + low * m
    # white balance, exposure, contrast, gamma, saturation
    x = x * torch.exp(N(b, 3, 1, 1) * 0.10 * s) * torch.exp(N(b, 1, 1, 1) * 0.22 * s)
    dark = P(0.15)
    x = x * (1 - dark * U(b, 1, 1, 1, lo=0.25, hi=0.6))
    mean = x.mean((1, 2, 3), keepdim=True)
    x = (x - mean) * torch.exp(N(b, 1, 1, 1) * 0.2 * s) + mean
    x = x.clamp(1e-4, 1) ** torch.exp(N(b, 1, 1, 1) * 0.2 * s)
    grey = x.mean(1, keepdim=True)
    x = grey + (x - grey) * U(b, 1, 1, 1, lo=0.5, hi=1.35)
    # sensor noise (worse in low light)
    x = x + N(b, 3, INPUT_H, INPUT_W) * (U(b, 1, 1, 1, hi=0.035) + dark * 0.04) * s
    return x.clamp(0, 1)


# ---- model --------------------------------------------------------------------------------------------------
class Embedder(nn.Module):
    """Backbone -> 128-d unit vector. Takes RGB in 0..1 (normalisation is inside, so the exported model does too)."""

    def __init__(self, backbone: str = "mobilenetv3_large_100", pretrained: bool = True, dim: int = EMBED_DIM):
        super().__init__()
        import timm
        self.backbone_name = backbone
        self.backbone = timm.create_model(backbone, pretrained=pretrained, num_classes=0)
        with torch.no_grad():           # pooled feature size differs from num_features on some timm models
            feat = self.backbone.eval()(torch.zeros(1, 3, INPUT_H, INPUT_W)).shape[1]
        self.fc = nn.Linear(feat, dim)
        self.bn = nn.BatchNorm1d(dim)
        self.register_buffer("mean", torch.tensor(MEAN).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(STD).view(1, 3, 1, 1))

    def forward(self, x):
        f = self.backbone((x - self.mean) / self.std)
        return F.normalize(self.bn(self.fc(f)).float(), dim=1)


class CosFace(nn.Module):
    """Large-margin cosine loss over illustration classes."""

    def __init__(self, dim: int, classes: int, s: float = 32.0, m: float = 0.25):
        super().__init__()
        self.w = nn.Parameter(torch.randn(classes, dim) * 0.01)
        self.s, self.m = s, m

    def forward(self, emb, labels):
        logits = emb @ F.normalize(self.w, dim=1).t()
        logits = logits - self.m * F.one_hot(labels, logits.shape[1])
        return F.cross_entropy(self.s * logits, labels)


def pair_loss(photo: torch.Tensor, clean: torch.Tensor, labels: torch.Tensor, temperature: float = 0.05):
    """Photo -> reference retrieval inside the batch, exactly the lookup done at scan time. Every reference with
    the same illustration counts as correct."""
    logits = photo @ clean.t() / temperature
    pos = labels[:, None] == labels[None, :]
    loss_a = torch.logsumexp(logits, 1) - torch.logsumexp(logits.masked_fill(~pos, -1e9), 1)
    loss_b = torch.logsumexp(logits, 0) - torch.logsumexp(logits.masked_fill(~pos, -1e9), 0)
    return 0.5 * (loss_a.mean() + loss_b.mean())


def load_checkpoint(path: str | Path, device="cpu") -> tuple[Embedder, dict]:
    ck = torch.load(path, map_location=device, weights_only=False)
    model = Embedder(ck["backbone"], pretrained=False)
    model.load_state_dict(ck["model"])
    return model.to(device).eval(), ck


# ---- evaluation ---------------------------------------------------------------------------------------------
@torch.no_grad()
def embed_rows(model, rows, data_dir, device, photo_seed: int | None = None, batch: int = 512, workers: int = 12,
               strength: float = 1.0) -> torch.Tensor:
    """Embeddings for rows: clean references, or (with photo_seed) one fixed synthetic photo of each."""
    ds = CardDataset(rows, data_dir, [0] * len(rows), jpeg_prob=0.3 if photo_seed is not None else 0.0)
    dl = torch.utils.data.DataLoader(ds, batch_size=batch, num_workers=workers, shuffle=False,
                                     worker_init_fn=lambda i: np.random.seed(1234 + i))
    g = torch.Generator(device=device).manual_seed(photo_seed) if photo_seed is not None else None
    out = []
    model.eval()
    for clean, src, _, _ in dl:
        with torch.autocast("cuda", enabled=device != "cpu"):
            x = photo_augment(src.to(device), g, strength) if g is not None else to_input(clean.to(device))
            out.append(model(x.contiguous(memory_format=torch.channels_last)).float())
    return torch.cat(out)


def top1(query: torch.Tensor, gallery: torch.Tensor, q_groups: list[int], g_groups: list[int]) -> float:
    """Share of queries whose nearest reference shows the same illustration."""
    gg = torch.tensor(g_groups, device=gallery.device)
    qg = torch.tensor(q_groups, device=gallery.device)
    hits = 0
    for i in range(0, len(query), 2048):
        nn_idx = (query[i:i + 2048] @ gallery.t()).argmax(1)
        hits += int((gg[nn_idx] == qg[i:i + 2048]).sum())
    return hits / max(len(query), 1)
