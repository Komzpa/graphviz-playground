#!/usr/bin/env python3
"""Do two arms landing on the same node trade places?

An arm that comes from the right must not land to the left of an arm that comes
from the left: to reach that landing the line has to climb over its neighbour,
and the crossing it draws is in neither the graph nor anyone's intent.

The bound is ZERO, not "no worse than upstream", and that distinction is the
whole point of this file. On
tests/graphs/concentrate-demo/distinct-shared-trunk-siblings-separate.dot under
-Gconcentrate=false, upstream aef8a6fd8 ALSO inverts one pair -- red b->d lands
at x=93.3 and black a->d at x=93.6 -- but 0.3 pt apart, stacked, so the crossing
is hidden inside the pile. Spreading those heads to 7 pt apart (78.3 and 85.3)
did not create the crossing; it dragged an existing one into the open. Operator,
2026-08-01: «лишний кроссинг».

So upstream is NOT a clean baseline here, and a gate calibrated to match it
would certify the defect. Separate the heads AND put them in source order, and
the count goes to zero -- strictly better than upstream, which is what a fix
should look like.

Usage: verify_arm_order.py DOT FIXTURE [FLAGS...]
Prints swapped_pairs; anything above zero is a crossing we are drawing on
purpose without meaning to.
"""
import subprocess,sys,json,math
from itertools import combinations
def load(b,p,flags):
    o=subprocess.run([b,"-Tjson",*flags,p],capture_output=True,timeout=300)
    if o.returncode: return {}
    j=json.loads(o.stdout.decode("utf-8","replace"))
    names={x["_gvid"]:x.get("name") for x in j.get("objects",[]) if "_gvid" in x}
    byn={}
    for e in j.get("edges",[]):
        if any(e.get(a) for a in ("arrowhead","arrowtail","dir")): continue
        pts=None
        for op in e.get("_draw_",[]):
            if op.get("op") in ("b","B"):
                pts=[tuple(q) for q in op.get("points",[])]; break
        if not pts or len(pts)<4: continue
        for stream,node,src in (("_hdraw_",e.get("head"),pts[0]),("_tdraw_",e.get("tail"),pts[-1])):
            tip=None
            for op in e.get(stream,[]):
                if op.get("op") in ("P","p"):
                    q=[tuple(v) for v in op.get("points",[])]
                    if len(q)==3:
                        cx=sum(v[0] for v in q)/3; cy=sum(v[1] for v in q)/3
                        tip=max(q,key=lambda v:(v[0]-cx)**2+(v[1]-cy)**2)
                    break
            if tip is None: continue
            byn.setdefault(names.get(node),[]).append((src,tip))
    return byn
def swaps(group):
    if len(group)<2: return 0
    xs=[t for _,t in group]
    ax=max(p[0] for p in xs)-min(p[0] for p in xs); ay=max(p[1] for p in xs)-min(p[1] for p in xs)
    pr=(lambda p:p[0]) if ax>=ay else (lambda p:p[1])
    n=0
    for (s1,t1),(s2,t2) in combinations(group,2):
        dt=pr(t1)-pr(t2); ds=pr(s1)-pr(s2)
        if abs(dt)<1e-6 or abs(ds)<1e-6: continue
        if (dt>0)!=(ds>0): n+=1
    return n
b,p=sys.argv[1],sys.argv[2]; flags=sys.argv[3:]
g=load(b,p,flags)
tot=sum(swaps(v) for v in g.values())
print(f"{' '.join(flags) or '(default)':22s} swapped_pairs={tot}")
for node,v in sorted(g.items()):
    s=swaps(v)
    if s: print(f"    at {node}: {s} swapped pair(s) among {len(v)} heads")
