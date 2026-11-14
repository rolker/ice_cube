"""Metrics and stake-out corners for chosen footprints. Writes out/res.pkl for the site plan."""
import pickle

import pyproj
from scipy.ndimage import binary_dilation
from scipy.spatial import cKDTree
from matplotlib.path import Path

from config import X0, Y0, SH, EPSG, OUT
from ground import *
from search import SP
from scipy.interpolate import RegularGridInterpolator
from pyproj import Geod
D=pickle.load(open(OUT/'dem.pkl','rb')); ob=obstacles()
Zn=D['nov'][1]; Z=Zn; Zo=D['oct'][1]
# name: (centre x, centre y, width, length, angle of long axis CCW from east), local metres
CAND={'A':(50,60.5,6,11,115),'B':(52.01,59.51,7,14,90),'C':(53,58.5,8,16,80)}
inv=pyproj.Transformer.from_crs(EPSG,4326,always_xy=True); g=Geod(ellps='WGS84')
r=jan()['rink']; pp=Path(np.c_[r[:,0]-X0,r[:,1]-Y0]); lym=pp.contains_points(np.c_[GX.ravel(),GY.ravel()]).reshape(GX.shape)
print('LY centroid',GX[lym].mean(),GY[lym].mean())
def report(m,name):
    o={}
    for k,zz in [('merged',Z),('oct',Zo),('nov',Zn)]:
        v=zz[m]; print(name,k,'nan',np.isnan(v).sum(),{a:round(float(b),3) for a,b in stats(v[~np.isnan(v)]).items()}); o[k]=stats(v[~np.isnan(v)])
    return o
res={'LY':{'stats':report(lym,'LY'),'thick':None}}
for n,(cx,cy,w,l,a) in CAND.items():
    m=rect_mask(cx,cy,w,l,a); s=report(m,n)
    c=corners(cx,cy,w,l,a)
    H=s['nov']['H']
    I=RegularGridInterpolator((yi,xi),Z,bounds_error=False)
    cd=H-I(np.c_[c[:,1],c[:,0]])
    b=m&binary_dilation(~m,iterations=2)
    dep=np.maximum(H-Z,0); 
    per=2*(w+l); frac10=(dep[b]>.10).mean(); frac15=(dep[b]>.15).mean()
    # min distance to obstacles
    P=np.c_[GX[m],GY[m]]; dists={}
    for k,p in ob.items():
        q=np.vstack([p,p[0]]); pts=np.vstack([np.c_[np.linspace(u[0],v[0],40),np.linspace(u[1],v[1],40)] for u,v in zip(q[:-1],q[1:])])
        dists[k]=round(float(cKDTree(pts).query(P)[0].min()),2)
    wall=(GX>54)&(Z<16.0)&~np.isnan(Z); dwall=cKDTree(np.c_[GX[wall],GY[wall]]).query(P)[0].min()
    ll=[inv.transform(x+X0,y+Y0) for x,y in c]
    ll2=[inv.transform(x+X0-SH[0],y+Y0-SH[1]) for x,y in c]
    az=g.inv(*ll[0],*ll[1])[0]
    dsp=cKDTree(P).query(SP)[0]
    res[n]={'cx':cx,'cy':cy,'w':w,'l':l,'ang':a,'stats':s,'corners':c.tolist(),'cdepth':cd.tolist(),'frac10':float(frac10),'frac15':float(frac15),'per':per,'d':dists,'dwall':float(dwall),'ll':ll,'ll2':ll2,'az':az%360,'dsp':float(dsp)}
    print(n,'corner depth',cd.round(3),'perim>10cm',round(frac10,2),'>15',round(frac15,2),'dists',dists,'wall',round(dwall,1),'az',round(az%360,1),'spigot',round(dsp,1))
pickle.dump(res,open(OUT/'res.pkl','wb'))
