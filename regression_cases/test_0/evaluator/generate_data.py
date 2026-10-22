"""Recreate original deterministic test0 data. NOT part of task input."""
from __future__ import annotations
import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"benchmark/datasets/brownian_2d/trajectories.csv"

def generate_bytes() -> bytes:
    cfg=json.loads((ROOT/"expected.json").read_text(encoding="utf8"))
    rng=np.random.default_rng(cfg["seed"])
    n=cfg["n_trajectories"]; steps=cfg["n_steps"]
    d=cfg["diffusion_coefficient"]; dt=cfg["dt"]; sigma=cfg["localization_sigma"]
    increments=rng.normal(0.0,np.sqrt(2*d*dt),size=(n,steps-1,2))
    paths=np.concatenate((np.zeros((n,1,2)),np.cumsum(increments,axis=1)),axis=1)
    positions=paths+rng.normal(0.0,sigma,size=(n,steps,2))
    sio=io.StringIO(newline="")
    wr=csv.writer(sio,lineterminator="\n")
    wr.writerow(["trajectory_id","time_index","split","x","y"])
    for tid in range(n):
        split="train" if tid<cfg["n_train"] else "validation"
        for t in range(steps):
            x,y=positions[tid,t]
            wr.writerow([tid,t,split,f"{x:.8f}",f"{y:.8f}"])
    return sio.getvalue().encode("utf8")

def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--check",action="store_true")
    a=p.parse_args()
    content=generate_bytes()
    if a.check:
        if not DATA.is_file() or DATA.read_bytes()!=content:
            p.error("frozen fixture data mismatch")
    else:
        DATA.parent.mkdir(parents=True,exist_ok=True)
        DATA.write_bytes(content)
    print(f"{len(content)} bytes, sha256={hashlib.sha256(content).hexdigest()}")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
