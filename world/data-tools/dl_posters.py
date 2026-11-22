import json, hashlib, sys, os, requests
from concurrent.futures import ThreadPoolExecutor
w=json.load(open('data/world.json'))
UA={"User-Agent":"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
def path(n): return f"data/raw/posters/{n['id']}.img"
def get(n):
    p=path(n)
    if os.path.exists(p) or not n.get('image'): return
    for _ in range(2):
        try:
            r=requests.get(n['image'],headers=UA,timeout=15)
            if r.status_code==200 and len(r.content)>500:
                open(p+'.tmp','wb').write(r.content); os.rename(p+'.tmp',p); return
        except Exception: pass
ns=[n for n in w['nodes'] if n.get('image')]
with ThreadPoolExecutor(12) as ex: list(ex.map(get,ns))
print("done",len(os.listdir('data/raw/posters')))
