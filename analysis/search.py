"""Search rink footprints over sizes, positions and angles. Needs out/dem.pkl from ground.py."""
import pickle

from scipy.ndimage import binary_dilation

from config import SPIGOT, OUT
from ground import *

SP = np.array(SPIGOT)
S=2 # 0.5 m search grid
def search(Z,w,l,buf=1.0,forb=None):
    z=Z[::S,::S]; x=xi[::S]; y=yi[::S]; f=forb[::S,::S]|np.isnan(z)
    zz=np.nan_to_num(z,nan=0.0); res=[]
    du=np.arange(-l/2+.25,l/2,.5); dv=np.arange(-w/2+.25,w/2,.5); U,V=np.meshgrid(du,dv); U=U.ravel();V=V.ravel()
    cxs=np.arange(44,63,.5); cys=np.arange(38,74,.5); CX,CY=np.meshgrid(cxs,cys); CX=CX.ravel();CY=CY.ravel()
    for ang in range(0,180,5):
        t=np.radians(ang); ou=U*np.cos(t)-V*np.sin(t); ov=U*np.sin(t)+V*np.cos(t)
        ix=np.rint((CX[:,None]+ou[None]-x[0])/.5).astype(int); iy=np.rint((CY[:,None]+ov[None]-y[0])/.5).astype(int)
        ok=(ix>=0)&(ix<len(x))&(iy>=0)&(iy<len(y)); good=ok.all(1)
        ix=ix[good];iy=iy[good];cx=CX[good];cy=CY[good]
        bad=f[iy,ix].any(1); ix=ix[~bad];iy=iy[~bad];cx=cx[~bad];cy=cy[~bad]
        if len(cx)==0: continue
        zv=zz[iy,ix]
        p99=np.percentile(zv,99,axis=1); p1=np.percentile(zv,1,axis=1); H=p99+.05
        dep=np.maximum(H[:,None]-zv,0)
        res.append(pd.DataFrame({'cx':cx,'cy':cy,'ang':ang,'w':w,'l':l,'relief':p99-p1,'H':H,'vol':dep.sum(1)*.25,'meand':dep.mean(1),'maxd':dep.max(1),'lowZ':zv.min(1)}))
    return pd.concat(res,ignore_index=True) if res else pd.DataFrame()
def distinct(r,n=4,minsep=3.5):
    r=r.sort_values('vol'); pick=[]
    for _,a in r.iterrows():
        if all(np.hypot(a.cx-b.cx,a.cy-b.cy)>minsep for b in pick):
            pick.append(a)
            if len(pick)==n: break
    return pd.DataFrame(pick)

def forbidden(Z, ob, buf=1.0):
    """Obstacles plus a buffer, plus the steep drop on the east side."""
    gy, gx = np.gradient(Z, RES)
    sl = np.hypot(gx, gy)
    st = binary_dilation((GX > 54) & (sl > .10), iterations=4)
    return mask_obs(ob, buf) | st


if __name__ == "__main__":
    D = pickle.load(open(OUT / "dem.pkl", "rb"))
    ob = obstacles()
    Z = D["nov"][1]
    for buf, sizes in [(2.0, [(6, 11), (7, 14)]), (1.0, [(6, 11), (7, 14), (8, 16), (10, 20)])]:
        forb = forbidden(Z, ob, buf)
        for w, l in sizes:
            r = search(Z, w, l, forb=forb)
            if len(r) == 0:
                print("buffer", buf, w, l, "no fit")
                continue
            t = distinct(r, 3)
            t["dsp"] = np.hypot(t.cx - SP[0], t.cy - SP[1])
            print("buffer", buf, w, l)
            print(t[["cx", "cy", "ang", "relief", "vol", "maxd", "dsp"]].round(2).to_string(index=False))
