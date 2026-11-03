"""Cache frozen encoder features, then train SAFE/ROAD/OTHER + CURB heads quickly."""

import os
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
from transformers import SegformerConfig
from transformers.models.segformer.modeling_segformer import SegformerDecodeHead, SegformerModel

from train_multihead import MultiHead, remap_main
from train_sanpo_segformer import DATA, FROZEN, MODEL, OUT, Sanpo

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "cache_multihead.pt"
EPOCHS = int(os.getenv("EPOCHS", "10"))


class Cached(Dataset):
    def __init__(self, feats, main, curb, idx):
        self.feats, self.main, self.curb, self.idx = feats, main, curb, idx

    def __len__(self):
        return len(self.idx)

    def __getitem__(self, i):
        j = self.idx[i]
        return [f[j] for f in self.feats], self.main[j], self.curb[j]


def main():
    torch.set_num_threads(8)
    sessions = [p.name for p in sorted(DATA.iterdir()) if p.is_dir()]
    train_s = [s for s in sessions if s not in FROZEN][:21]
    base = Sanpo(train_s, False)
    limit = int(os.getenv("LIMIT", "240"))
    base = torch.utils.data.Subset(base, list(range(min(limit, len(base)))))
    if not CACHE.exists():
        model = MultiHead(OUT / "segformer_sanpo_b0_v6.pt")
        levels = [[] for _ in range(4)]; mains = []; curbs = []
        with torch.no_grad():
            for i in range(len(base)):
                x, y = base[i]
                hs = model.backbone(pixel_values=x[None], output_hidden_states=True).hidden_states
                for k in range(4):
                    levels[k].append(hs[k][0])
                mains.append(remap_main(y[None])[0])
                curbs.append((y == 2).long())
                if i % 50 == 0:
                    print("cache", i, len(base), flush=True)
        feats = [torch.stack(l) for l in levels]
        torch.save({"feats": feats, "main": torch.stack(mains), "curb": torch.stack(curbs)}, CACHE)
    d = torch.load(CACHE, map_location="cpu")
    n0 = int(os.getenv('SAMPLES', '9999'))
    feats = [f[:n0] for f in d["feats"]]
    mains, curbs = d["main"][:n0], d["curb"][:n0]
    del d
    if os.getenv('SMALLFEAT','0')=='1':
        feats=[torch.nn.functional.avg_pool2d(f,2) for f in feats]
    print("cached", len(mains), [tuple(f.shape) for f in feats], flush=True)

    n = min(len(mains), int(os.getenv('SAMPLES', '9999')))
    idx = []
    for i in range(n):
        idx.extend([i] * (5 if int((curbs[i] == 1).sum()) > 40 else 1))
    print("train", len(idx), flush=True)
    train = Cached(feats, mains, curbs, idx)
    tl = DataLoader(train, batch_size=8, shuffle=True, num_workers=0)

    cfg = SegformerConfig.from_pretrained(MODEL)
    cfg.num_labels = 3
    head_main = SegformerDecodeHead(cfg)
    cfg2 = SegformerConfig.from_pretrained(MODEL)
    cfg2.num_labels = 2
    head_curb = SegformerDecodeHead(cfg2)
    opt = torch.optim.AdamW(list(head_main.parameters()) + list(head_curb.parameters()), lr=2e-4, weight_decay=.01)
    w_main = torch.tensor([1., 2.5, 10.]); w_curb = torch.tensor([1., 40.])

    for ep in range(EPOCHS):
        head_main.train(); head_curb.train(); tot = 0.
        for st, (hs, ym, yc) in enumerate(tl):
            opt.zero_grad()
            main = head_main(hs)
            curb = head_curb(hs)
            shape = main.shape[-2:]
            ym = F.interpolate(ym[:, None].float(), size=shape, mode="nearest")[:, 0].long()
            yc = F.interpolate(yc[:, None].float(), size=shape, mode="nearest")[:, 0].long()
            l1 = F.cross_entropy(main, ym, weight=w_main, ignore_index=255)
            road = ym == 2
            if road.any():
                l1 = l1 + 3.0 * F.softmax(main, 1)[:, 1][road].mean()
            l2 = F.cross_entropy(curb, yc, weight=w_curb)
            p = F.softmax(curb, 1)[:, 1]
            dice = 1 - (2 * (p * yc).sum() + 1) / (p.sum() + yc.sum() + 1)
            loss = l1 + l2 + dice
            loss.backward(); opt.step(); tot += float(loss.detach())
        print(f"ep {ep} {tot/max(1,len(tl)):.4f}", flush=True)
        torch.save({"backbone": torch.load(OUT / "segformer_sanpo_b0_v6.pt", map_location="cpu")["state_dict"],
                    "head_main": head_main.state_dict(), "head_curb": head_curb.state_dict(),
                    "model": str(OUT / "segformer_sanpo_b0_v6.pt")},
                   OUT / "segformer_multihead_v1.pt")
    print("saved", OUT / "segformer_multihead_v1.pt", flush=True)


if __name__ == "__main__":
    main()
