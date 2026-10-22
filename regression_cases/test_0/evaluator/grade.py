"""Test0 private post-run oracle; never materialize into scientific task."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def grade_payload(obj:dict)->dict:
    """Private numeric oracle for an in-memory, evaluator-side estimate."""
    truth=json.loads((ROOT/"expected.json").read_text(encoding="utf8"))
    tol=truth["relative_error_tolerance"]
    try:
        if not isinstance(obj,dict):
            raise ValueError("scoring input must be an object")
        d=obj["D_estimate"]
        if isinstance(d,bool) or not isinstance(d,(int,float)):
            raise ValueError("D_estimate must be a JSON number")
        if not math.isfinite(d) or d<=0:
            raise ValueError("D_estimate must be finite and positive")
    except (KeyError,TypeError,ValueError) as exc:
        return {"id":"test_0","passed":False,"tolerance":tol,"error":str(exc)}
    rel=abs(d-truth["diffusion_coefficient"])/truth["diffusion_coefficient"]
    warnings=[f"missing report field: {key}" for key in ("method","validation")
              if not isinstance(obj.get(key),str) or not obj[key].strip()]
    return {"id":"test_0","passed":rel<=tol+1e-12,"relative_error":round(rel,12),
            "tolerance":tol,"warnings":warnings}


def grade(submission:Path)->dict:
    try:
        obj=json.loads(submission.read_text(encoding="utf8"))
    except (OSError,TypeError,ValueError) as exc:
        truth=json.loads((ROOT/"expected.json").read_text(encoding="utf8"))
        return {"id":"test_0","passed":False,
                "tolerance":truth["relative_error_tolerance"],"error":str(exc)}
    return grade_payload(obj)

def main()->int:
    p=argparse.ArgumentParser()
    p.add_argument("submission",type=Path)
    a=p.parse_args()
    result=grade(a.submission)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result["passed"] else 1

if __name__=="__main__":
    raise SystemExit(main())
