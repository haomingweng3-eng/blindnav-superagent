"""Boundary/curb-focused decoder adaptation on top of v5."""

import os
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import ConcatDataset, DataLoader, Dataset
from transformers.models.segformer import SegformerForSemanticSegmentation

from train_sanpo_segformer import DATA, FROZEN, OUT, Sanpo

ROOT = Path(__file__).resolve().parents[1]
EPOCHS = int(os.getenv("EPOCHS", "3"))


class BoundaryBoost(Dataset):
    def __init__(self, base, indices):
        self.base = base
        self.indices = indices

    def __len__(self):
        return len(self.indices)

    def __getitem__(self, i):
        return self.base[self.indices[i]]


def main():
    torch.set_num_threads(6)
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    train_s = [s for s in sessions if s not in FROZEN][:21]
    val_s = [s for s in sessions if s not in FROZEN][21:27]
    base = Sanpo(train_s, True)
    idx = []
    for i in range(len(base)):
        _, y = base[i]
        n = int((y == 2).sum())
        idx.extend([i] * (5 if n > 40 else 1))
    print("base", len(base), "boosted", len(idx), flush=True)

    ck = torch.load(OUT / "segformer_sanpo_b0_v5.pt", map_location="cpu")
    model = SegformerForSemanticSegmentation.from_pretrained(
        ck["model"], num_labels=5, ignore_mismatched_sizes=True)
    model.load_state_dict(ck["state_dict"])
    for p in model.segformer.parameters():
        p.requires_grad = False
    train = BoundaryBoost(base, idx)
    val = Sanpo(val_s, False)
    device = torch.device("cpu"); model.to(device)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=5e-5, weight_decay=.01)
    weights = torch.tensor([1., 2.5, 60., 8., 1.], device=device)
    tl = DataLoader(train, batch_size=4, shuffle=True, num_workers=0)
    vl = DataLoader(val, batch_size=4, num_workers=0)

    def objective(logits, y):
        ce = F.cross_entropy(logits, y, weight=weights, ignore_index=255)
        road = y == 3
        if road.any():
            ce = ce + 2.0 * F.softmax(logits, 1)[:, 1][road].mean()
        p2 = F.softmax(logits, 1)[:, 2]
        g2 = (y == 2).float()
        dice = 1 - (2 * (p2 * g2).sum() + 1) / (p2.sum() + g2.sum() + 1)
        return ce + dice

    best = 1e9
    for ep in range(EPOCHS):
        model.train(); tot = 0.
        for st, (x, y) in enumerate(tl):
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            logits = model(pixel_values=x).logits
            logits = F.interpolate(logits, size=y.shape[-2:], mode="bilinear", align_corners=False)
            loss = objective(logits, y); loss.backward(); opt.step(); tot += float(loss.detach())
            if st % 25 == 0:
                print(f"ep {ep} step {st}/{len(tl)} {float(loss.detach()):.4f}", flush=True)
        model.eval(); v = 0.
        with torch.no_grad():
            for x, y in vl:
                x, y = x.to(device), y.to(device)
                logits = model(pixel_values=x).logits
                logits = F.interpolate(logits, size=y.shape[-2:], mode="bilinear", align_corners=False)
                v += float(objective(logits, y))
        v /= max(1, len(vl))
        print(f"ep {ep} train {tot/max(1,len(tl)):.4f} val {v:.4f}", flush=True)
        if v < best:
            best = v
            torch.save({"state_dict": model.state_dict(), "model": ck["model"], "classes": 5},
                       OUT / "segformer_sanpo_b0_v6.pt")
    print("saved v6 best", best, flush=True)


if __name__ == "__main__":
    main()
