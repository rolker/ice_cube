"""Compare the measured home-base (PPP) to MassDOT offset with the published
ITRF2014 to NAD83(2011) transformation at the yard."""
import numpy as np
import pyproj

from config import X0, Y0, SH, EPSG

lon, lat = pyproj.Transformer.from_crs(EPSG, 4326, always_xy=True).transform(X0 + 50, Y0 + 50)
print("measured PPP to MassDOT  E %.3f  N %.3f  U %.3f" % SH)
for t in pyproj.transformer.TransformerGroup("EPSG:7912", "EPSG:6319", always_xy=True).transformers[:3]:
    o = t.transform(lon, lat, 15.0, 2026.84)
    print("%-60s E %.3f  N %.3f  U %.3f" % (t.description[:60], (o[0] - lon) * np.cos(np.radians(lat)) * 111320,
                                           (o[1] - lat) * 111132, o[2] - 15))
