"""Site settings. Private values (where the yard is, where the data lives) come from
site_config.json, which is git-ignored. Copy site_config.example.json to start."""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
_path = Path(os.environ.get("ICECUBE_CONFIG", HERE / "site_config.json"))
if not _path.exists():
    raise SystemExit(f"Missing {_path}. Copy site_config.example.json to site_config.json and fill it in.")
C = json.loads(_path.read_text())

X0, Y0 = C["origin_utm19n"]                 # local grid origin, MassDOT-frame UTM 19N
EPSG = C.get("epsg", 32619)
SH = tuple(C["ppp_to_massdot"])             # add to home-base (PPP) E, N, up to get MassDOT frame
ZB = C.get("oct_minus_nov_ground", 0.0)     # Oct ground minus Nov ground after SH
CONTROL_HOME = tuple(C["control_local_home"])  # deck corner, home frame, local metres
SPIGOT = tuple(C["spigot_local"])
WALL_LABEL = tuple(C["wall_label_local"])
CAMPER = [tuple(p) for p in C["camper_local"]]  # three corners; the fourth is completed
CLICK_UTC_OFFSET_H = C.get("clicks_utc_offset_hours", 0)
DATA = Path(C["data_dir"]).expanduser()
FILES = C["files"]
OUT = Path(C.get("out_dir", "out"))
if not OUT.is_absolute():
    OUT = HERE / OUT
OUT.mkdir(parents=True, exist_ok=True)


def f(key):
    """Path to a data file named in the config."""
    return DATA / FILES[key]
