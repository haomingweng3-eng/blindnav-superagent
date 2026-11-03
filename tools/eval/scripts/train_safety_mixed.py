"""Mixed SANPO + Cityscapes safety training with false-safe penalty."""

from __future__ import annotations

import os
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import ConcatDataset, DataLoader, Dataset
from transformers.models.segformer import SegformerForSemanticSegmentation

from train_sanpo_segformer import DATA, FROZEN, MODEL, OUT, SIZE, Sanpo

ROOT = Path(__file__).resolve().parents[1]
CITY = ROOT / "data" / "cityscapes" / "train_png"
SUFFIX = os.getenv("SUFFIX", "_v4")
EPOCHS = int(os.getenv("EPOCHS", "8"))


class CityPng(Dataset):
    def __init__(self, items, train):
        self.items = items
        self.train = train

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        fp, mp = self.items[i]
        im = Image.open(fp).convert("RGB").resize(SIZE, Image.BILINEAR)
        a = np.array(Image.open(mp))[:, :, 0]
        out = np.full(a.shape, 255, np.uint8)
        out[a == 1] = 1
        out[(a == 0) | (a == 9)] = 3
        known = (a < 19) & (a != 0) & (a != 1) & (a != 9)
        out[known] = 4
        out = np.array(Image.fromarray(out).resize(SIZE, Image.NEAREST))
        x = np.asarray(im, np.float32) / 255.0
        x = (x - np.array([.485, .456, .406], np.float32)) / np.array([.229, .224, .225], np.float32)
        if self.train and random.random() < .5:
            x = x[:, ::-1].copy(); out = out[:, ::-1].copy()
        return torch.from_numpy(x.transpose(2, 0, 1)), torch.from_numpy(out.astype(np.int64))


def main():
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    train_s = [s for s in sessions if s not in FROZEN][:21]
    val_s = [s for s in sessions if s not in FROZEN][21:27]
    city = sorted((CITY / "images").glob("*.png"))
    city_items = [(p, CITY / "masks" / p.name) for p in city if (CITY / "masks" / p.name).exists()]
    cut = int(len(city_items) * .88)
    train = ConcatDataset([Sanpo(train_s, True), CityPng(city_items[:cut], True)])
    val = ConcatDataset([Sanpo(val_s, False), CityPng(city_items[cut:], False)])
    print("train", len(train), "val", len(val), flush=True)

    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = SegformerForSemanticSegmentation.from_pretrained(
        MODEL, num_labels=5, ignore_mismatched_sizes=True).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=6e-5, weight_decay=.01)
    weights = torch.tensor([1.0, 2.5, 15.0, 8.0, 1.0], device=device)
    tl = DataLoader(train, batch_size=4, shuffle=True, num_workers=0)
    vl = DataLoader(val, batch_size=4, shuffle=False, num_workers=0)

    def objective(logits, y):
        loss = F.cross_entropy(logits, y, weight=weights, ignore_index=255)
        road = y == 3
        if road.any():
            loss = loss + 2.0 * F.softmax(logits, 1)[:, 1][road].mean()
        return loss

    best = 1e9
    for ep in range(EPOCHS):
        model.train(); tot = 0.
        for st, (x, y) in enumerate(tl):
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            logits = model(pixel_values=x).logits
            logits = F.interpolate(logits, size=y.shape[-2:], mode="bilinear", align_corners=False)
            loss = objective(logits, y)
            loss.backward(); opt.step(); tot += float(loss.detach())
            if st % 25 == 0:
                print(f"ep {ep} step {st}/{len(tl)} loss {float(loss.detach()):.4f}", flush=True)
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
            torch.save({"state_dict": model.state_dict(), "model": MODEL, "classes": 5},
                       OUT / f"segformer_sanpo_b0{SUFFIX}.pt")
    print("saved", OUT / f"segformer_sanpo_b0{SUFFIX}.pt", "best", best, flush=True)


if __name__ == "__main__":
    main()
