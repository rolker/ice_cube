"""Map of a rover log by RTK status. Needs out/gga.pkl from log_quality.py."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from config import SPIGOT, OUT
from ground import *

t=pd.read_pickle(OUT/'gga.pkl'); ob=obstacles()
fig,ax=plt.subplots(figsize=(7.5,9))
for n,p in ob.items():
    q=np.vstack([p,p[0]]); ax.fill(q[:,0],q[:,1],color='#c9d2da',ec='#46596a',lw=.8,zorder=1); ax.text(p[:,0].mean(),p[:,1].mean(),n,ha='center',va='center',fontsize=8,color='#17252f',zorder=5)
f=t[t.q==4]; b=t[t.q!=4]
ax.scatter(f.x,f.y,s=2,color='#2a78d6',label=f'RTK fixed ({len(f)/len(t)*100:.0f}% of samples)',zorder=2,linewidths=0)
ax.scatter(b.x,b.y,s=7,color='#c8501e',label=f'Float or worse ({len(b)/len(t)*100:.0f}%)',zorder=3,linewidths=0)
RINK=(50,60.5,6,11,115)  # option A
c=corners(*RINK); q=np.vstack([c,c[0]])
tt=np.radians(RINK[4]); u=(t.x-RINK[0])*np.cos(tt)+(t.y-RINK[1])*np.sin(tt); v=-(t.x-RINK[0])*np.sin(tt)+(t.y-RINK[1])*np.cos(tt)
inside=(abs(u)<=RINK[3]/2)&(abs(v)<=RINK[2]/2)
ax.plot(q[:,0],q[:,1],color='#17252f',lw=2,zorder=4,label=f'Rink option A ({(t.q[inside]==4).mean()*100:.1f}% fixed)')
ax.scatter([SPIGOT[0]],[SPIGOT[1]],s=45,facecolor='white',edgecolor='#17252f',zorder=6); ax.text(SPIGOT[0]-.5,SPIGOT[1],'spigot',ha='right',va='center',fontsize=8,zorder=6)
ax.set_aspect('equal'); ax.set_xlim(38,66); ax.set_ylim(33,74); ax.set_xticks([]); ax.set_yticks([])
for s in ax.spines.values(): s.set_visible(False)
ax.plot([41,46],[35,35],color='#17252f',lw=2); ax.text(43.5,35.5,'5 m',ha='center',fontsize=8)
ax.legend(loc='upper center',bbox_to_anchor=(.5,.0),ncol=3,frameon=False,fontsize=8.5,markerscale=3)
ax.set_title('Rover track by RTK status',loc='left',fontsize=12,fontweight='bold')
plt.tight_layout(); plt.savefig(OUT/'fix-map.png',dpi=160)
