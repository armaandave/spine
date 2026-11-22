"""Stage A: near, anchor, clusters (unnamed), sphere -> data/raw/stage_a.json"""
import json, numpy as np, glob
S="/Users/armaandave/projects/spine-similarity-data/"
w=json.load(open('data/world_v0.json')); nodes=w['nodes']
keys=json.load(open(S+'vectors/v4-hybrid.keys.json')); V=np.load(S+'vectors/v4-hybrid.npy').astype('float32')
V/=np.linalg.norm(V,axis=1,keepdims=True)
row={k:i for i,k in enumerate(keys)}
X=V[[row[n['key']] for n in nodes]]
M=w['mineCount']; assert all(n['mine'] for n in nodes[:M])
# artists
art={}
for p in glob.glob(S+'details*.jsonl'):
    for l in open(p):
        if not l.strip(): continue
        r=json.loads(l)
        ids=set(map(str,r.get('artist_ids') or []))
        if not ids:
            try:
                for c in json.loads((r.get('details') or {}).get('artist_credits') or '[]'):
                    if c.get('artist_id'): ids.add(str(c['artist_id']))
            except Exception: pass
        if ids: art[r['key']]=ids
sim={k:set(map(str,v)) for k,v in json.load(open(S+'artist_graph.json'))['similar'].items()}
A=[art.get(n['key'],set()) if n['type']=='music' else set() for n in nodes]
Xm=X[:M]; C=Xm@Xm.T
mus=[i for i in range(M) if A[i]]
for a in mus:
    for b in mus:
        if a==b: continue
        if A[a]&A[b]: C[a,b]+=1.0
        elif any(sim.get(x,set())&A[b] for x in A[a]) or any(sim.get(y,set())&A[a] for y in A[b]): C[a,b]+=0.8
np.fill_diagonal(C,-9)
near=np.argsort(-C,axis=1)[:,:8]
# anchor
anchor=np.argmax(X[M:]@Xm.T,axis=1)
# clusters
from sklearn.cluster import AgglomerativeClustering
K=16
lab=AgglomerativeClustering(n_clusters=K,linkage='ward').fit_predict(Xm)
# sphere
import umap
Y=umap.UMAP(n_components=3,metric='cosine',n_neighbors=15,min_dist=0.3,random_state=42).fit_transform(Xm)
Y-=Y.mean(0)
u,sv,vt=np.linalg.svd(Y,full_matrices=False); Y=u*np.sqrt(len(Y))  # whiten so no axis dominates
Y/=np.linalg.norm(Y,axis=1,keepdims=True)
print("sphere mean resultant",np.linalg.norm(Y.mean(0)),"cov eig",np.linalg.eigvalsh(np.cov(Y.T)))
json.dump({"near":near.tolist(),"anchor":(anchor).tolist(),"lab":lab.tolist(),"sphere":Y.tolist()},open('data/raw/stage_a.json','w'))
for c in range(K):
    ids=[i for i in range(M) if lab[i]==c]
    from collections import Counter
    print(f"\n== {c} n={len(ids)}",dict(Counter(nodes[i]['type'] for i in ids)))
    print(" | ".join(f"{nodes[i]['title']} ({nodes[i]['maker'][:18]})" for i in ids[:34]))
