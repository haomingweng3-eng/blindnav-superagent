"""Fine-tune SegFormer-B0 on SANPO remapped traversability labels."""

from __future__ import annotations

import os
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader, Dataset
from transformers.models.segformer import SegformerForSemanticSegmentation

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "sanpo"
OUT = ROOT / "models"
MODEL = "nvidia/segformer-b0-finetuned-cityscapes-1024-1024"
SUFFIX=os.getenv('SUFFIX','')
BOUND_W=float(os.getenv('BOUND_W','5.0'))
COND_W=float(os.getenv('COND_W','1.0'))
SAFE_W=float(os.getenv('SAFE_W','2.5'))
FALSE_SAFE_W=float(os.getenv('FALSE_SAFE_W','0.0'))
EPOCHS=int(os.getenv('EPOCHS','3'))
FROZEN = {"5LlqRK-hWoDLSW5MmoLjKj6uQtZMKjb9",
          "972O8sd5HpUbGeEE_UAb1g0z1OZUtfHl",
          "GxMb4zhAvoM5jbF54kfcs8wxTL4fqNnT"}
SAFE = {3, 5, 6, 17}
BOUNDARY = {2}
CONDITIONAL = {1, 30}
# 0=ignore, 1=SAFE, 2=BOUNDARY, 3=CONDITIONAL, 4=NON
SIZE = (512, 288)  # W,H


def remap(mask: np.ndarray) -> np.ndarray:
    out = np.full(mask.shape, 255, dtype=np.uint8)
    out[np.isin(mask, list(SAFE))] = 1
    out[np.isin(mask, list(BOUNDARY))] = 2
    out[np.isin(mask, list(CONDITIONAL))] = 3
    known = np.isin(mask, list(range(1, 31))) & ~np.isin(mask, list(SAFE | BOUNDARY | CONDITIONAL))
    out[known] = 4
    return out


class Sanpo(Dataset):
    def __init__(self, sessions: list[str], train: bool):
        self.items = []
        for sid in sessions:
            fd = DATA / sid / "video_frames"
            md = DATA / sid / "segmentation_masks"
            for fp in sorted(fd.glob("*.png")):
                mp = md / fp.name
                if mp.exists():
                    self.items.append((fp, mp))
        self.train = train

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        fp, mp = self.items[i]
        im = Image.open(fp).convert("RGB").resize(SIZE, Image.BILINEAR)
        mask = np.array(Image.open(mp))[:, :, 0]
        mask = remap(mask)
        mask = np.array(Image.fromarray(mask, mode="L").resize(SIZE, Image.NEAREST))
        x = np.asarray(im, dtype=np.float32) / 255.0
        x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
        if self.train and random.random() < 0.5:
            x = x[:, ::-1].copy(); mask = mask[:, ::-1].copy()
        return torch.from_numpy(x.transpose(2, 0, 1)), torch.from_numpy(mask.astype(np.int64))


def main():
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    train_sessions = [s for s in sessions if s not in FROZEN][:21]
    val_sessions = [s for s in sessions if s not in FROZEN][21:27]
    print("train_sessions", len(train_sessions), "val_sessions", len(val_sessions), flush=True)
    train = Sanpo(train_sessions, True)
    val = Sanpo(val_sessions, False)
    print("train_frames", len(train), "val_frames", len(val), flush=True)
    device = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
    model = SegformerForSemanticSegmentation.from_pretrained(
        MODEL, num_labels=5, ignore_mismatched_sizes=True).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=6e-5, weight_decay=0.01)
    weights = torch.tensor([1.0, SAFE_W, BOUND_W, COND_W, 1.0], device=device)
    train_loader = DataLoader(train, batch_size=4, shuffle=True, num_workers=0)
    val_loader = DataLoader(val, batch_size=4, shuffle=False, num_workers=0)
    OUT.mkdir(exist_ok=True)
    best = 1e9
    def objective(logits, y):
        loss = F.cross_entropy(logits, y, weight=weights, ignore_index=255)
        if FALSE_SAFE_W > 0:
            road = y == 3
            if road.any():
                p_safe = F.softmax(logits, dim=1)[:, 1]
                loss = loss + FALSE_SAFE_W * p_safe[road].mean()
        return loss
    for epoch in range(EPOCHS):
        model.train()
        total = 0.0
        for step, (x, y) in enumerate(train_loader):
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            logits = model(pixel_values=x).logits
            logits = F.interpolate(logits, size=y.shape[-2:], mode="bilinear", align_corners=False)
            loss = objective(logits, y)
            loss.backward()
            opt.step()
            total += float(loss.detach())
            if step % 20 == 0:
                print(f"epoch {epoch} step {step}/{len(train_loader)} loss {float(loss.detach()):.4f}", flush=True)
        model.eval()
        vloss = 0.0
        with torch.no_grad():
            for x, y in val_loader:
                x, y = x.to(device), y.to(device)
                logits = model(pixel_values=x).logits
                logits = F.interpolate(logits, size=y.shape[-2:], mode="bilinear", align_corners=False)
                vloss += float(objective(logits, y))
        vloss /= max(1, len(val_loader))
        print(f"epoch {epoch} train {total/max(1,len(train_loader)):.4f} val {vloss:.4f}", flush=True)
        if vloss < best:
            best = vloss
            torch.save({"state_dict": model.state_dict(), "model": MODEL, "classes": 5},
                       OUT / f"segformer_sanpo_b0{SUFFIX}.pt")
    print("saved", OUT / f"segformer_sanpo_b0{SUFFIX}.pt", "best", best, flush=True)


if __name__ == "__main__":
    main()
