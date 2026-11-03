"""Fast decoder adaptation to Cityscapes road/sidewalk with SANPO replay."""

import os
from pathlib import Path

import torch
import torch.nn.functional as F
from torch.utils.data import ConcatDataset, DataLoader
from transformers.models.segformer import SegformerForSemanticSegmentation

from train_safety_mixed import CITY, CityPng
from train_sanpo_segformer import DATA, FROZEN, OUT, Sanpo

ROOT = Path(__file__).resolve().parents[1]
EPOCHS = int(os.getenv("EPOCHS", "4"))
OUT_CKPT = OUT / "segformer_sanpo_b0_v5.pt"


def main():
    torch.set_num_threads(4)
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    train_s = [s for s in sessions if s not in FROZEN][:21]
    val_s = [s for s in sessions if s not in FROZEN][21:27]
    city = sorted((CITY / "images").glob("*.png"))
    city_items = [(p, CITY / "masks" / p.name) for p in city if (CITY / "masks" / p.name).exists()]
    cut = int(len(city_items) * .88)

    ck = torch.load(OUT / "segformer_sanpo_b0_v3.pt", map_location="cpu")
    model = SegformerForSemanticSegmentation.from_pretrained(
        ck["model"], num_labels=5, ignore_mismatched_sizes=True)
    model.load_state_dict(ck["state_dict"])
    for p in model.segformer.parameters():
        p.requires_grad = False
    for p in model.decode_head.parameters():
        p.requires_grad = True

    train = ConcatDataset([
        CityPng(city_items[:cut], True),
        Sanpo(train_s, True),
    ])
    val = ConcatDataset([CityPng(city_items[cut:], False), Sanpo(val_s, False)])
    print("train", len(train), "val", len(val), flush=True)

    device = torch.device("cpu")
    model.to(device)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=1e-4, weight_decay=.01)
    weights = torch.tensor([1., 3., 15., 10., 1.], device=device)
    tl = DataLoader(train, batch_size=4, shuffle=True, num_workers=0)
    vl = DataLoader(val, batch_size=4, num_workers=0)

    def objective(logits, y):
        loss = F.cross_entropy(logits, y, weight=weights, ignore_index=255)
        road = y == 3
        if road.any():
            loss = loss + 4.0 * F.softmax(logits, 1)[:, 1][road].mean()
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
            torch.save({"state_dict": model.state_dict(), "model": ck["model"], "classes": 5}, OUT_CKPT)
    print("saved", OUT_CKPT, "best", best, flush=True)


if __name__ == "__main__":
    main()
