"""Build the site plan page.

Usage: python site_plan/build.py site_plan/template.html out/rink-site-plan.html
The output contains real coordinates, so keep it out of the repository."""
import json
import pickle
import sys
from pathlib import Path as _P

sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pyproj

from config import X0, Y0, SH, ZB, EPSG, SPIGOT, WALL_LABEL, OUT
from ground import *
from surveys import oct_things, jan

D=pickle.load(open(OUT/'dem.pkl','rb')); res=pickle.load(open(OUT/'res.pkl','rb')); ob=obstacles()
ZB_=ZB
Z=D['nov'][1]-ZB_
Z=np.where((GX>53)&(GY<40.5),np.nan,Z)  # hide porch-step artifact
V=(40,64.5,35,73); S=13  # px per m
sx=lambda x:(x-V[0])*S; sy=lambda y:(V[3]-y)*S; W=(V[1]-V[0])*S; H=(V[3]-V[2])*S
lev=[15.0,16.0,16.05,16.10,16.15,16.20,16.25,16.30,16.35,17.0]
Zc=np.ma.masked_invalid(Z)
fig,ax=plt.subplots(); cs=ax.contourf(GX,GY,Zc,levels=lev)
paths=[]
def d_of(p):
    out=[];v=p.vertices;c=p.codes
    if len(v)==0: return ''
    for (x,y),k in zip(v,c if c is not None else [1]+[2]*(len(v)-1)):
        if k==1: out.append(f"M{sx(x):.1f} {sy(y):.1f}")
        elif k==2: out.append(f"L{sx(x):.1f} {sy(y):.1f}")
        elif k==79: out.append("Z")
    return ''.join(out)
for i,p in enumerate(cs.get_paths()):
    paths.append(f'<path class="b{i}" fill-rule="evenodd" d="{d_of(p)}"/>')
cl=ax.contour(GX,GY,Zc,levels=[16.05,16.1,16.15,16.2,16.25,16.3])
lines=''.join(f'<path class="ct" d="{d_of(p)}"/>' for p in cl.get_paths())
obs=''
lab={'swing set':(0,0),'garden shed':(0,0),'boat shed':(1.8,-0.2),'deck':(0,0),'arbor':(0,-1.9),'camper':(0,0)}
for k,p in ob.items():
    d='M'+'L'.join(f"{sx(x):.1f} {sy(y):.1f}" for x,y in p)+'Z'
    cx,cy=p.mean(0); dx,dy=lab[k]
    cx=min(max(cx+dx,V[0]+2.2),V[1]-1.5)
    obs+=f'<path class="ob" d="{d}"/><text class="obt" x="{sx(cx):.1f}" y="{sy(cy+dy)+3:.1f}" text-anchor="middle">{k}</text>'
th=oct_things()
def ptx(x,y): return sx(x-X0),sy(y-Y0)
sp=(sx(SPIGOT[0]),sy(SPIGOT[1])); wall=th[th.Remarks.astype(str).str.contains('stone wall')][['X','Y']].values
wl='M'+'L'.join(f"{ptx(x,y)[0]:.1f} {ptx(x,y)[1]:.1f}" for x,y in wall)
r=jan()['rink']; ly='M'+'L'.join(f"{sx(x-X0):.1f} {sy(y-Y0):.1f}" for x,y in r[:-1,:2])+'Z'
inv=pyproj.Transformer.from_crs(EPSG,4326,always_xy=True)
opts={}
names={'A':'Same size','B':'Bigger','C':'Largest fit'}
def rng(s,k,f=1.0): return s['oct'][k]*f
for n in 'ABC':
    o=res[n]; c=np.array(o['corners']); ctr=c.mean(0)
    rows=[]
    for i,(x,y) in enumerate(c):
        a=np.degrees(np.arctan2(y-ctr[1],x-ctr[0]))
        comp=['E','NE','N','NW','W','SW','S','SE'][int(((a+22.5)%360)//45)]
        rows.append(dict(c=comp,mx=x+X0,my=y+Y0,hx=x+X0-SH[0],hy=y+Y0-SH[1],mlon=o['ll'][i][0],mlat=o['ll'][i][1],hlon=o['ll2'][i][0],hlat=o['ll2'][i][1],dep=o['cdepth'][i]*100))
    opts[n]=dict(name=names[n],w=o['w'],l=o['l'],az=round(o['az']),pts=' '.join(f"{sx(x):.1f},{sy(y):.1f}" for x,y in c),c=rows,
      H=(o['stats']['nov']['H']-ZB_),vol=o['stats']['nov']['vol'],volr=rng(o['stats'],'vol'),max=o['stats']['nov']['maxd']*100,maxr=rng(o['stats'],'maxd',100),
      rel=o['stats']['nov']['relief']*100,relr=rng(o['stats'],'relief',100),area=o['stats']['nov']['area'],mean=o['stats']['nov']['meand']*100,sp=o['dsp'],d=o['d'],wall=o['dwall'],frac=o['frac10'])
L=res['LY']['stats']
ly_m=dict(vol=L['nov']['vol'],volr=rng(L,'vol'),max=L['nov']['maxd']*100,maxr=rng(L,'maxd',100),rel=L['nov']['relief']*100,relr=rng(L,'relief',100),area=L['nov']['area'],mean=L['nov']['meand']*100)
# hover grid at 0.5 m
gz=np.round(Z[::2,::2],3); gx0,gy0=float(xi[0]),float(yi[0])
hover=dict(x0=gx0,y0=gy0,d=.5,nx=gz.shape[1],ny=gz.shape[0],z=[None if np.isnan(v) else float(v) for v in gz.ravel()])
data=dict(opts=opts,ly=ly_m,hover=hover,V=V,S=S)
svg=f'''<svg id="map" viewBox="0 0 {W:.0f} {H:.0f}" role="img" aria-label="Elevation map of the back yard with candidate rink footprints">
<rect class="void" width="{W:.0f}" height="{H:.0f}"/>{''.join(paths)}<g>{lines}</g>{obs}
<path class="wall" d="{wl}"/><text class="obt" x="{W-6:.0f}" y="{sy(WALL_LABEL[1])+18:.1f}" text-anchor="end">stone wall</text>
<circle class="spig" cx="{sp[0]:.1f}" cy="{sp[1]:.1f}" r="4.5"/><text class="obt" x="{sp[0]-8:.1f}" y="{sp[1]+3:.1f}" text-anchor="end">spigot</text>
<path class="ly" d="{ly}"/><polygon id="fp" class="fp" points=""/>
<g id="mk"></g>
<g transform="translate({W-40:.0f},{H-70:.0f})"><path class="na" d="M0 22V-6M-6 4L0 -8L6 4"/><text class="obt" x="0" y="-13" text-anchor="middle">N</text></g>
<g transform="translate(22,{H-20:.0f})"><path class="na" d="M0 -5V0H{5*S}V-5"/><text class="obt" x="{2.5*S}" y="-8" text-anchor="middle">5 m</text></g>
</svg>'''
tpl=open(sys.argv[1]).read().replace('/*DATA*/',json.dumps(data,separators=(',',':'))).replace('<!--SVG-->',svg)
open(sys.argv[2],"w").write(tpl); print(len(tpl), {k:(round(v['vol'],2),round(v['volr'],2),round(v['max']),round(v['H'],3)) for k,v in opts.items()}, ly_m)
