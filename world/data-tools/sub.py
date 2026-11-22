import json,numpy as np
from sklearn.cluster import AgglomerativeClustering
S="/Users/armaandave/projects/spine-similarity-data/"
w=json.load(open('data/world_v0.json'));nodes=w['nodes'];M=w['mineCount']
keys=json.load(open(S+'vectors/v4-hybrid.keys.json'));V=np.load(S+'vectors/v4-hybrid.npy').astype('float32');V/=np.linalg.norm(V,axis=1,keepdims=True)
row={k:i for i,k in enumerate(keys)}
a=json.load(open('data/raw/stage_a.json'));lab=np.array(a['lab'])
ids=[i for i in range(M) if lab[i]==4]
X=V[[row[nodes[i]['key']] for i in ids]]
sl=AgglomerativeClustering(n_clusters=3,linkage='ward').fit_predict(X)
for c in range(3):
    m=[ids[j] for j in range(len(ids)) if sl[j]==c]
    print(c,len(m),' | '.join(nodes[i]['title'][:28]+' ('+nodes[i]['maker'][:14]+')' for i in m[:45]))
