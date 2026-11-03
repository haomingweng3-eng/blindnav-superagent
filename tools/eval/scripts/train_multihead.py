"""Multi-head safety model: shared encoder, SAFE/ROAD/OTHER + CURB heads."""

import os
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from transformers import SegformerConfig
from transformers.models.segformer.modeling_segformer import SegformerDecodeHead, SegformerModel

from train_sanpo_segformer import DATA, FROZEN, OUT, Sanpo

ROOT = Path(__file__).resolve().parents[1]
EPOCHS = int(os.getenv("EPOCHS", "5"))


def remap_main(y):
    out = torch.zeros_like(y)
    out[y == 1] = 1
    out[y == 3] = 2
    out[y == 255] = 255
    return out


class MultiHead(nn.Module):
    def __init__(self, ckpt):
        super().__init__()
        ck = torch.load(ckpt, map_location="cpu")
        self.backbone = SegformerModel.from_pretrained(ck["model"])
        missing, unexpected = self.backbone.load_state_dict(
            {k[len("segformer."):]: v for k, v in ck["state_dict"].items() if k.startswith("segformer.")},
            strict=False)
        print("backbone missing", len(missing), "unexpected", len(unexpected), flush=True)
        cfg = SegformerConfig.from_pretrained(ck["model"])
        cfg.num_labels = 3
        self.head_main = SegformerDecodeHead(cfg)
        cfg2 = SegformerConfig.from_pretrained(ck["model"])
        cfg2.num_labels = 2
        self.head_curb = SegformerDecodeHead(cfg2)
        for p in self.backbone.parameters():
            p.requires_grad = False

    def forward(self, x):
        hs = self.backbone(pixel_values=x, output_hidden_states=True).hidden_states
        return self.head_main(hs), self.head_curb(hs)


def main():
    torch.set_num_threads(8)
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    train_s = [s for s in sessions if s not in FROZEN][:21]
    val_s = [s for s in sessions if s not in FROZEN][21:27]
    base = Sanpo(train_s, True)
    idx = []
    for i in range(len(base)):
        _, y = base[i]
        n = int((y == 2).sum())
        idx.extend([i] * (8 if n > 40 else 1))
    print("train", len(idx), flush=True)
    train = torch.utils.data.Subset(base, idx)
    val = Sanpo(val_s, False)
    device = torch.device("cpu")
    model = MultiHead(OUT / "segformer_sanpo_b0_v6.pt").to(device)
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=2e-4, weight_decay=.01)
    tl = DataLoader(train, batch_size=4, shuffle=True, num_workers=0)
    vl = DataLoader(val, batch_size=4, num_workers=0)
    w_main = torch.tensor([1., 2.5, 10.], device=device)
    w_curb = torch.tensor([1., 40.], device=device)

    def loss_fn(main, curb, y):
        ym = remap_main(y)
        l1 = F.cross_entropy(main, ym, weight=w_main, ignore_index=255)
        road = ym == 2
        if road.any():
            l1 = l1 + 3.0 * F.softmax(main, 1)[:, 1][road].mean()
        yc = (y == 2).long()
        l2 = F.cross_entropy(curb, yc, weight=w_curb)
        p = F.softmax(curb, 1)[:, 1]
        dice = 1 - (2 * (p * yc).sum() + 1) / (p.sum() + yc.sum() + 1)
        return l1 + l2 + dice

    best = 1e9
    for ep in range(EPOCHS):
        model.train(); tot = 0.
        for st, (x, y) in enumerate(tl):
            x, y = x.to(device), y.to(device)
            opt.zero_grad()
            main, curb = model(x)
            main = F.interpolate(main, size=y.shape[-2:], mode="bilinear", align_corners=False)
            curb = F.interpolate(curb, size=y.shape[-2:], mode="bilinear", align_corners=False)
            loss = loss_fn(main, curb, y)
            loss.backward(); opt.step(); tot += float(loss.detach())
            if st % 25 == 0:
                print(f"ep {ep} step {st}/{len(tl)} {float(loss.detach()):.4f}", flush=True)
        model.eval(); v = 0.
        with torch.no_grad():
            for x, y in vl:
                x, y = x.to(device), y.to(device)
                main, curb = model(x)
                main = F.interpolate(main, size=y.shape[-2:], mode="bilinear", align_corners=False)
                curb = F.interpolate(curb, size=y.shape[-2:], mode="bilinear", align_corners=False)
                v += float(loss_fn(main, curb, y))
        v /= max(1, len(vl))
        print(f"ep {ep} train {tot/max(1,len(tl)):.4f} val {v:.4f}", flush=True)
        if v < best:
            best = v
            torch.save({"backbone": model.backbone.state_dict(),
                        "head_main": model.head_main.state_dict(),
                        "head_curb": model.head_curb.state_dict(),
                        "model": str(OUT / "segformer_sanpo_b0_v6.pt")},
                       OUT / "segformer_multihead_v1.pt")
    print("saved", best, flush=True)


if __name__ == "__main__":
    main()
