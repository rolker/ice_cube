"""Find still-pole survey points in a continuous log and score them against SW Maps clicks.
Needs out/gga.pkl from log_quality.py."""
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from config import CLICK_UTC_OFFSET_H, OUT
from surveys import nov

pd.set_option('display.width',200)


def detect(d,win=2.0,minlen=8,hr=.03,vs=.012):
    t=d.t.values;x=d.X.values;y=d.Y.values;z=d.z.values;q=d.q.values; out=[];i=0;N=len(d)
    while i<N:
        if q[i]!=4: i+=1; continue
        j=i
        while j+1<N and t[j+1]-t[i]<win: j+=1
        if j-i+1>=minlen and (q[i:j+1]==4).all() and np.hypot(x[i:j+1]-x[i:j+1].mean(),y[i:j+1]-y[i:j+1].mean()).max()<hr and z[i:j+1].std()<vs:
            k=j
            while k+1<N and q[k+1]==4 and np.hypot(x[k+1]-x[i:k+1].mean(),y[k+1]-y[i:k+1].mean())<hr and abs(z[k+1]-z[i:k+1].mean())<2.5*vs and t[k+1]-t[k]<1.2: k+=1
            out.append((t[i],t[k],t[k]-t[i],x[i:k+1].mean(),y[i:k+1].mean(),z[i:k+1].mean(),z[i:k+1].std(),k-i+1)); i=k+1
        else: i+=1
    return pd.DataFrame(out,columns=['t0','t1','dur','X','Y','z','zs','n'])
if __name__=='__main__':
    d=pd.read_pickle(OUT/'gga.pkl'); n=nov()
    n['t']=pd.to_datetime(n.Time.str[:23],format='%m/%d/%Y %H:%M:%S.%f'); n['ts']=n.t.dt.hour*3600+n.t.dt.minute*60+n.t.dt.second+n.t.dt.microsecond/1e6-CLICK_UTC_OFFSET_H*3600
    D=detect(d); print('dwells',len(D),'dur',D.dur.describe().round(1).to_dict())
    c=n[n['Fix ID']==4]
    hit=[];dz=[];dxy=[]
    for _,r in c.iterrows():
        k=D[(D.t0-1.0<=r.ts)&(D.t1+1.0>=r.ts)]
        if len(k):
            hit.append(k.index[0]); dz.append(k.z.iloc[0]-r.Elevation); dxy.append(np.hypot(k.X.iloc[0]-r.X,k.Y.iloc[0]-r.Y))
    print('fixed clicks',len(c),'matched',len(dz),'recall %',round(len(dz)/len(c)*100,1),'dz med',round(np.median(dz),4),'dz rms',round(np.sqrt(np.mean(np.square(dz))),4),'dxy med',round(np.median(dxy),3))
    ex=D.drop(index=set(hit)); print('unmatched dwells',len(ex),'pct',round(len(ex)/len(D)*100,1))
    # classify extra dwells vs ground surface from clicks
    kd=cKDTree(c[['X','Y']].values)
    dd,ii=kd.query(ex[['X','Y']].values); ex=ex.assign(dn=dd,gz=ex.z.values-c.Elevation.values[ii])
    print('extra near click (<1.2 m): ',(ex.dn<1.2).sum(),'dz med',round(ex[ex.dn<1.2].gz.median(),3),' on ground (|dz|<10cm)',((ex.dn<1.2)&(abs(ex.gz)<.10)).sum(),' high >10cm',((ex.dn<1.2)&(ex.gz>.10)).sum(),' low',((ex.dn<1.2)&(ex.gz<-.10)).sum(),' far from any click',(ex.dn>=1.2).sum())
    print(ex.gz.describe().round(3).to_dict()); print(pd.cut(ex.gz,[-3,-.1,.1,.3,.6,1,3]).value_counts().sort_index().to_dict())
    # why missed
    mz=[]
    for _,r in c.iterrows():
        if not len(D[(D.t0-1.0<=r.ts)&(D.t1+1.0>=r.ts)]): 
            w=d[(d.t>r.ts-2)&(d.t<r.ts+2)]; mz.append((round(r.ts-d.t.iloc[0]),len(w),round(w.z.std(),3) if len(w) else None,round(np.hypot(w.X-w.X.mean(),w.Y-w.Y.mean()).max(),3) if len(w) else None))
    print('missed',len(mz),mz[:25])
    D.to_pickle(OUT/'dwells.pkl')
