"""Ground model, obstacles and rink-footprint helpers on a local 0.25 m grid.

Run directly to build out/dem.pkl and print leave-one-out errors."""
import pickle

import numpy as np
import pandas as pd

from config import X0, Y0, SH, ZB, CONTROL_HOME, CAMPER, OUT
from surveys import nov, oct_topo, oct_things, jan
from scipy.spatial import cKDTree, ConvexHull
from matplotlib.path import Path


def points(which='merged'):
    o=oct_topo(); o=o[o.Remarks.isna()]
    a=pd.DataFrame({'x':o.X-X0,'y':o.Y-Y0,'z':o.Elevation,'s':'oct'})
    n=nov(); ctrl=(abs(n.X-X0-CONTROL_HOME[0])<.1)&(abs(n.Y-Y0-CONTROL_HOME[1])<.1)
    n=n[~ctrl&(n['Fix ID']==4)]
    b=pd.DataFrame({'x':n.X+SH[0]-X0,'y':n.Y+SH[1]-Y0,'z':n.Elevation+SH[2]+ZB,'s':'nov'})
    d=pd.concat([a,b],ignore_index=True)
    if which=='oct': d=d[d.s=='oct']
    if which=='nov': d=d[d.s=='nov']
    return d
def clean(d,r=1.5,k=3.0):
    t=cKDTree(d[['x','y']].values); keep=np.ones(len(d),bool)
    for i,nb in enumerate(t.query_ball_point(d[['x','y']].values,r)):
        nb=[j for j in nb if j!=i]
        if len(nb)>=4:
            A=np.c_[np.ones(len(nb)),d.x.values[nb]-d.x.values[i],d.y.values[nb]-d.y.values[i]]
            c=np.linalg.lstsq(A,d.z.values[nb],rcond=None)[0]
            res=d.z.values[nb]-A@c; s=1.4826*np.median(abs(res))+.02
            if abs(d.z.values[i]-c[0])>k*s and abs(d.z.values[i]-c[0])>.15: keep[i]=False
    return d[keep].reset_index(drop=True),(~keep).sum()
def fit(d,qx,qy,h=1.0,maxr=2.5):
    t=cKDTree(d[['x','y']].values); Z=np.full(qx.shape,np.nan); N=np.zeros(qx.shape,int)
    q=np.c_[qx.ravel(),qy.ravel()]
    for i,nb in enumerate(t.query_ball_point(q,maxr)):
        if len(nb)<5: continue
        dx=d.x.values[nb]-q[i,0]; dy=d.y.values[nb]-q[i,1]; w=np.exp(-.5*(dx**2+dy**2)/h**2)
        A=np.c_[np.ones(len(nb)),dx,dy]; W=np.sqrt(w)[:,None]
        c=np.linalg.lstsq(A*W,d.z.values[nb]*W[:,0],rcond=None)[0]
        Z.flat[i]=c[0]; N.flat[i]=len(nb)
    return Z
def loo(d,h=1.0,maxr=2.5):
    t=cKDTree(d[['x','y']].values); e=[]
    for i,nb in enumerate(t.query_ball_point(d[['x','y']].values,maxr)):
        nb=[j for j in nb if j!=i]
        if len(nb)<5: continue
        dx=d.x.values[nb]-d.x.values[i]; dy=d.y.values[nb]-d.y.values[i]; w=np.exp(-.5*(dx**2+dy**2)/h**2)
        A=np.c_[np.ones(len(nb)),dx,dy]; W=np.sqrt(w)[:,None]
        c=np.linalg.lstsq(A*W,d.z.values[nb]*W[:,0],rcond=None)[0]; e.append(d.z.values[i]-c[0])
    return np.array(e)
RES=.25
xi=np.arange(35,75,RES); yi=np.arange(30,80,RES); GX,GY=np.meshgrid(xi,yi)
def dem(which):
    d,drop=clean(points(which)); Z=fit(d,GX,GY); return d,Z,drop
# obstacles
def obstacles():
    j=jan(); th=oct_things(); ob={}
    pl=lambda a:np.c_[np.array(a)[:,0]-X0,np.array(a)[:,1]-Y0]
    sw=th[th.Remarks.astype(str).str.contains('swing moved')][['X','Y']].values; ob['swing set']=pl(sw[ConvexHull(sw).vertices])
    ob['garden shed']=pl(j['garden shed'][:,:2]); ob['boat shed']=pl(j['boat shed'][:,:2]); ob['deck']=pl(j['deck corner 1'][:,:2])
    a=th[th.Remarks.astype(str).str.strip()=='arbor'][['X','Y']].values; ob['arbor']=pl(np.vstack([j['arbor'][:,:2],a]))
    ob['arbor']=ob['arbor'][ConvexHull(ob['arbor']).vertices]
    A,B,C=(np.array(p) for p in CAMPER);D=C+(B-A)
    ob['camper']=np.array([A,B,D,C])
    return ob
def mask_obs(ob,buf):
    P=np.c_[GX.ravel(),GY.ravel()]; m=np.zeros(GX.shape,bool)
    for k,p in ob.items():
        c=Path(p).contains_points(P,radius=0).reshape(GX.shape)
        # distance buffer
        pts=[]
        q=np.vstack([p,p[0]])
        for a,b in zip(q[:-1],q[1:]):
            n=int(np.hypot(*(b-a))/.1)+2; pts.append(np.c_[np.linspace(a[0],b[0],n),np.linspace(a[1],b[1],n)])
        dist=cKDTree(np.vstack(pts)).query(P)[0].reshape(GX.shape)
        m|=c|(dist<buf)
    return m
def stats(z,tmin=.05,cell=RES**2):
    H=np.percentile(z,99)+tmin
    dep=np.maximum(H-z,0)
    return dict(area=z.size*cell,relief=np.percentile(z,99)-np.percentile(z,1),H=H,vol=dep.sum()*cell,meand=dep.mean(),maxd=dep.max())
def rect_mask(cx,cy,w,l,ang):
    t=np.radians(ang); c,s=np.cos(t),np.sin(t)
    u=(GX-cx)*c+(GY-cy)*s; v=-(GX-cx)*s+(GY-cy)*c
    return (abs(u)<=l/2)&(abs(v)<=w/2)
def corners(cx,cy,w,l,ang):
    t=np.radians(ang); c,s=np.cos(t),np.sin(t)
    return np.array([[cx+u*c-v*s,cy+u*s+v*c] for u,v in [(-l/2,-w/2),(l/2,-w/2),(l/2,w/2),(-l/2,w/2)]])


if __name__ == "__main__":
    out = {}
    for w in ["merged", "oct", "nov"]:
        d, Z, drop = dem(w)
        e = loo(d)
        out[w] = (d, Z)
        print(w, "points", len(d), "dropped", drop, "LOO rms", e.std().round(3),
              "robust SD", (1.4826 * np.median(abs(e - np.median(e)))).round(3))
    pickle.dump(out, open(OUT / "dem.pkl", "wb"))
