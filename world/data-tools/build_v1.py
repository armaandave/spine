"""Build data/world.json (v1) from data/world_v0.json + stage A (build_v1a.py).
uv run --no-project --with numpy --with pillow --with requests --with umap-learn --with scikit-learn python data/build_v1.py
Posters must be downloaded first (dl_posters.py)."""
import json, os, re, colorsys, numpy as np
from datetime import datetime, timezone
from PIL import Image
D="data/"
w=json.load(open(D+"world_v0.json")); nodes=w["nodes"]; M=w["mineCount"]
a=json.load(open(D+"raw/stage_a.json"))
lab=a["lab"]; sphere=np.array(a["sphere"]); anchor=a["anchor"]; near=a["near"]

# merge stage-A clusters (ward K=16) + split rap; names/colors by reading members
import numpy as np
from sklearn.cluster import AgglomerativeClustering
S="/Users/armaandave/projects/spine-similarity-data/"
keys=json.load(open(S+"vectors/v4-hybrid.keys.json")); V=np.load(S+"vectors/v4-hybrid.npy").astype("float32")
V/=np.linalg.norm(V,axis=1,keepdims=True); row={k:i for i,k in enumerate(keys)}
rap=[i for i in range(M) if lab[i]==4]
sl=AgglomerativeClustering(n_clusters=3,linkage="ward").fit_predict(V[[row[nodes[i]["key"]] for i in rap]])
# sub-cluster order from reading: 0 underground, 1 rage/trap, 2 big names (label by representative)
final=[None]*M
NAMES={ # key -> (name,color)
 "anime":("anime & hand-drawn","#c4553a"),"scifi":("space & dystopia","#2f5f8f"),
 "fantasy":("swords & sorcery","#8c6d3a"),"altpop":("alt-pop & rnb","#c46f93"),
 "rap0":("underground rap","#5d6b3b"),"rap1":("rage rap","#e0533d"),"rap2":("rap royalty","#d4a23f"),
 "ambition":("ambition & obsession","#862f3c"),"capes":("capes & cowls","#44508f"),
 "horror":("modern horror","#74306a"),"family":("pixar & talking animals","#5aa3c2"),
 "crime":("crime & paranoia","#8f7a68"),"spies":("spies & heists","#b7a24c"),
 "comedy":("feel-good comedy","#d98a5b"),"world":("slow world cinema","#4f8c82"),"tv":("prestige tv","#a8a39a")}
MAP={0:"anime",1:"scifi",2:"fantasy",11:"fantasy",3:"altpop",5:"ambition",6:"capes",13:"capes",7:"horror",8:"family",9:"crime",10:"spies",12:"comedy",14:"world",15:"tv"}
order=list(NAMES)
for i in range(M):
    if lab[i]==4: continue
    final[i]=MAP[lab[i]]
for j,i in enumerate(rap): final[i]=f"rap{sl[j]}"
# the three rap sub-clusters were read in the order sl label 0,1,2 only in a run with this exact seed-free ward; ward is deterministic.
cid={k:n for n,k in enumerate(order)}
cluster=[cid[f] for f in final]
clusters=[{"id":cid[k],"name":NAMES[k][0],"color":NAMES[k][1],"members":[i for i in range(M) if final[i]==k]} for k in order]

def blurb(t):
    t=(t or "").strip()
    if len(t)<=300: return t
    cut=t[:300]; m=[x.end() for x in re.finditer(r"[.!?][\"')\]]*(?=\s|$)",cut)]
    if m and m[-1]>=60: return cut[:m[-1]].strip()
    return cut[:cut.rfind(" ")].strip()

TYPECOL={"movie":"#5a5a60","tv":"#6a6558","book":"#6b5b4d","game":"#4d6358","anime":"#5f5670","manga":"#5f5670","music":"#58606b"}
def dominant(img):
    im=img.convert("RGB").resize((48,48)); arr=np.asarray(im).reshape(-1,3).astype("float32")/255
    mx=arr.max(1); mn=arr.min(1); sat=(mx-mn)/np.maximum(mx,1e-6)
    d=mx-mn+1e-9; r,g,b=arr.T
    h=np.where(mx==r,((g-b)/d)%6,np.where(mx==g,(b-r)/d+2,(r-g)/d+4))/6
    ok=(sat>0.25)&(mx>0.2)
    if ok.sum()<20: m=arr.mean(0)
    else:
        bins=(h*12).astype(int)%12*3+np.minimum((sat*3).astype(int),2)
        score=np.bincount(bins[ok],weights=(sat[ok]**1.5)*(0.4+mx[ok]),minlength=36)
        b0=score.argmax(); sel=ok&(bins==b0); m=arr[sel].mean(0)
        hh,ss,vv=colorsys.rgb_to_hsv(*m); m=colorsys.hsv_to_rgb(hh,min(ss,0.85),min(max(vv,0.35),0.9))
    return "#%02x%02x%02x"%tuple(int(round(x*255)) for x in m)

def cover(img,w_,h_):
    sw,sh=img.size; s=max(w_/sw,h_/sh); nw,nh=max(w_,round(sw*s)),max(h_,round(sh*s))
    img=img.resize((nw,nh),Image.LANCZOS); l,t=(nw-w_)//2,(nh-h_)//2
    return img.crop((l,t,l+w_,t+h_))

class Atlas:
    def __init__(s,name,cw,ch,cols,rows,q):
        s.name,s.cw,s.ch,s.cols,s.rows,s.q=name,cw,ch,cols,rows,q; s.sheets=[]; s.n=0; s.cur=None
    def add(s,cell):
        per=s.cols*s.rows; sh,k=divmod(s.n,per)
        if k==0:
            s.flush(); s.cur=Image.new("RGB",(s.cw*s.cols,s.ch*s.rows),(0,0,0)); s.sheets.append(f"data/atlas/{s.name}{sh:02d}.jpg")
        c,r=k%s.cols,k//s.cols; s.cur.paste(cell,(c*s.cw,r*s.ch)); s.n+=1; return [sh,c,r]
    def flush(s):
        if s.cur is not None: s.cur.save(D+"atlas/"+os.path.basename(s.sheets[-1]),quality=s.q,optimize=True); s.cur=None

os.makedirs(D+"atlas",exist_ok=True)
base=Atlas("s",96,144,21,14,80); hi=Atlas("h",240,360,10,6,82)
out=[]; nimg=0
for n in nodes:
    i=n["id"]; n=dict(n); p=f"{D}raw/posters/{i}.img"; img=None
    if os.path.exists(p):
        try: img=Image.open(p); img.load(); img=img.convert("RGB")
        except Exception: img=None
    n["poster"]=n["posterHi"]=None
    if img:
        nimg+=1; alb=n["type"]=="music"
        cell=Image.new("RGB",(96,144)); cell.paste(cover(img,96,96) if alb else cover(img,96,144),(0,0)); n["poster"]=base.add(cell)
        if n["mine"]:
            hc=Image.new("RGB",(240,360)); hc.paste(cover(img,240,240) if alb else cover(img,240,360),(0,0)); n["posterHi"]=hi.add(hc)
        n["color"]=dominant(img)
    else: n["color"]=TYPECOL.get(n["type"],"#555")
    if i<M:
        n["near"]=near[i]; n["anchor"]=None; n["cluster"]=cluster[i]; n["sphere"]=[round(x,4) for x in sphere[i]]
    else:
        an=anchor[i-M]; n["near"]=[]; n["anchor"]=an; n["cluster"]=cluster[an]
        rng=np.random.default_rng(i); v=sphere[an]+rng.normal(0,0.035,3); v/=np.linalg.norm(v); n["sphere"]=[round(float(x),4) for x in v]
    n["blurb"]=blurb(n["blurb"]); n.pop("image",None); out.append(n)
base.flush(); hi.flush()
world=dict(w); world.update(v=1,generated=datetime.now(timezone.utc).isoformat(timespec="seconds"),nodes=out,clusters=clusters,
  atlas={"sheets":base.sheets,"cellW":96,"cellH":144,"cols":21},atlasHi={"sheets":hi.sheets,"cellW":240,"cellH":360,"cols":10})
open(D+"world.json.tmp","w").write(json.dumps(world,ensure_ascii=False,separators=(",",":"))); os.replace(D+"world.json.tmp",D+"world.json")
print("images",nimg,"base sheets",len(base.sheets),"hi sheets",len(hi.sheets))
