# Research Digest: ice_cube project

<!-- Last full refresh: 2026-11-21 -->
<!-- Run /research --refresh periodically to re-survey stale entries -->

External research on tools, methods and services for this project. Generic facts only:
nothing about the survey site, its location or the owner's accounts (see `CLAUDE.md`).
Items marked **(unverified)** were not confirmed against an official source.

## GNSS post-processing with RTKLIB-EX

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [RTKLIB-EX repo](https://github.com/rtklibexplorer/RTKLIB), [Quectel forum: LG290P raw is RTCM](https://forums.quectel.com/t/how-can-i-convert-lg290p-raw-data-to-rinex/45050), [SparkFun LG290P software](https://docs.sparkfun.com/SparkFun_LG290P_Quadband_GNSS_RTK_Breakout/software_overview/)

Key takeaways:
- RTKLIB-EX (formerly demo5): latest release v2.5.1 (2026-06-22); `main` still active as of 2026-11-21. Builds with CMake on Linux; headless tools `convbin`, `rnx2rtkp` (with a `.conf`), `str2str`, `rtkrcv`, so it suits a container.
- u-blox F9P: UBX-RXM-RAWX/SFRBX supported natively (`convbin -r ubx`), RTKLIB-EX's main target.
- Quectel LG290P: no proprietary parser in `src/rcv/`. Its raw output is RTCM 3.3 MSM4/MSM7, so use `convbin -r rtcm3 -tr <approx time>` (RTCM needs an approximate start time for week rollover). Users report this works; no official compatibility statement.
- Log MSM7 (full-resolution phase), 1005/1006 and ephemeris (1019/1020/1042/1046), or fetch broadcast nav separately.
- Python: `pyrtklib`, `georinex`, `gnss_lib_py` exist; calling the RTKLIB binaries via subprocess with a generated `.conf` is the most robust path.
- A rover against a nearby base (baseline of metres) needs only broadcast ephemeris; precise products matter for the base's own coordinate and long CORS baselines.

**Relevance**: the processing pipeline can be one container wrapping RTKLIB-EX for both u-blox and RTCM-only receivers.

---

## GNSS data and product sources after the NGS CORS data move

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [NGS NCN data page (retirement notice)](https://geodesy.noaa.gov/CORS/data.shtml), [NOAA NCN on AWS](https://registry.opendata.aws/noaa-ncn), [IGS products and latencies](https://www.igs.org/products), [CDDIS orbit products](https://cddis.nasa.gov/Data_and_Derived_Products/GNSS/orbit_products.html)

Key takeaways:
- NGS retired `geodesy.noaa.gov/corsdata/` on 2026-11-01 (HTTP 410). CORS RINEX, GPS broadcast nav, SP3 and published coordinates now come from the NOAA Open Data bucket `noaa-cors-pds` (anonymous HTTPS/S3). Old scripts and notes need updating.
- Daily CORS files appear within hours **(unverified exact latency)**; UFCORS still serves partial-day or resampled files.
- Multi-GNSS broadcast nav and IGS ultra-rapid (3-9 h), rapid (17-41 h) and final (12-19 days) products: BKG, anonymous (verified).
- CDDIS needs a NASA Earthdata login (`~/.netrc` plus cookie jar); prefer BKG or the NOAA bucket for unattended jobs.
- CODE and Wuhan multi-GNSS rapid products and OSB biases for PPP-AR via anonymous FTP **(unverified; not reachable during the survey)**.

**Relevance**: the pipeline's fetch step should target the NOAA bucket and BKG, not the retired NGS path or CDDIS.

---

## Base station coordinate: PPP and online services

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [CSRS-PPP updates](https://webapp.csrs-scrs.nrcan-rncan.gc.ca/geod/tools-outils/ppp-update.php), [NGS new datums](https://geodesy.noaa.gov/datums/newdatums/delayed-release.shtml)

Key takeaways:
- NGS OPUS: static, 2 h or more, gives NAD83(2011) and ITRF. Web form only; no documented submission API **(unverified; scripted form POSTs are used in the community)**.
- NRCan CSRS-PPP: v5 since 2025-05-14, free account, results by email, batch script on request, no published REST API.
- PRIDE PPP-AR and Ginan: open-source, headless Linux PPP (with ambiguity resolution), heavier than RTKLIB but better PPP; container-friendly.
- NSRS modernisation (NATRF2022/NAPGD2022) expected 2026-27 and not final as of 2026-11-21: record frame and epoch in every output.

**Relevance**: options for tracking a home base's coordinate without depending on a manual web form.

---

## Combining elevation data with uncertainty

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [Calder & Mayer 2003, doi:10.1029/2002GC000486](https://doi.org/10.1029/2002GC000486), [USGS Lidar Base Spec tables](https://www.usgs.gov/ngp-standards-and-specifications/lidar-base-specification-tables), [USGS interswath consistency](https://www.usgs.gov/ngp-standards-and-specifications/interswath-consistency-check-ssis)

Key takeaways:
- Solving per-pass/per-survey biases from crossovers is standard under other names: multibeam crossline checks and patch tests (IHO S-44), LiDAR strip adjustment, altimetry crossover adjustment with a datum (zero-mean) constraint.
- CUBE propagates per-sounding TPU but assumes independence; correlated error is handled upstream by crossline adjustment.
- USGS Lidar Base Spec QL1/QL2: swath-overlap difference <= 0.08 m, smooth-surface precision <= 0.06 m, non-vegetated RMSEz <= 0.10 m.
- GUM / ASPRS 2023 budgets keep the systematic part from shrinking with n, as a bias variance weighted by pass share does.
- Merging a LiDAR DEM with RTK: treat it as one more survey with one bias (absorbs datum, geoid, epoch) and low weight; it is a ground prior, not an ice measurement.
- Alternative: Gaussian process / least-squares collocation with bias parameters (`z = A b + s + n`, e.g. Matérn kernel plus nugget, i.e. universal kriging) gives biases, surface and full posterior covariance in one solve; O(n^3) on a few thousand cell averages is cheap. Useful as a cross-check mode beside the two-stage estimator.
- Output: BAG (HDF5) carries elevation plus uncertainty; a two-band COG or GeoPackage is enough for small areas.

**Relevance**: confirms the estimator's error model against established practice and points to a full-covariance alternative for growth and volume sums.

---

## Public elevation and imagery data APIs

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [TNM Access API](https://tnmaccess.nationalmap.gov/api/v1/docs), [3DEP on AWS](https://registry.opendata.aws/usgs-lidar/), [py3dep](https://pypi.org/project/py3dep/), [seamless-3dep](https://pypi.org/project/seamless-3dep/), [GRANIT bare-earth DEM service](https://nhgeodata.unh.edu/nhgeodata/rest/services/ImageServices/LiDAR_Bare_Earth_DEM_NH_2022/ImageServer), [NGS web services](https://geodesy.noaa.gov/web_services/), [NCAT API](https://geodesy.noaa.gov/web_services/ncat/), [Geoid API](https://geodesy.noaa.gov/web_services/geoid.shtml)

Key takeaways:
- USGS 3DEP point clouds: TNM Access API returns LAZ tile URLs by bbox; `s3://usgs-lidar-public` (EPT, anonymous) allows bbox crops with PDAL `readers.ept`. Public domain.
- 3DEP DEMs (1 m, 1/3"): TNM API, the 3DEP ImageServer, `py3dep` 0.19.0 or `seamless-3dep` 0.5.2 (as of 2026-11-21). NAVD88/GEOID18 heights.
- NAIP imagery (0.6 m, every 2-3 years, public domain) via TNM API, AWS or Planetary Computer.
- State GIS services (e.g. NH GRANIT ArcGIS REST: bare-earth DEM, DSM, parcels, orthophotos) are marked "Not for legal use" and ask for a citation; the full licence was not found, so check before republishing derived imagery.
- NGS geoid API (GEOID18, JSON, keyless) and NCAT (NADCON5/VERTCON3, SPC/UTM/XYZ); NCAT does not convert ellipsoidal to orthometric heights. HTDP has no public REST API **(unverified)**; PROJ/pyproj covers most NAD83(2011)-ITRF transforms.
- These services see the queried bbox or position: run queries in the user's own runtime, never in public CI logs or issues.

**Relevance**: automatic ground priors and reference data for any survey area, with a privacy rule for where queries run.

---

## Low-cost Google Cloud architecture

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [Cloud Run pricing](https://cloud.google.com/run/pricing), [Free Tier list](https://cloud.google.com/free/docs/free-cloud-features), [Cloud Run quotas (32 MiB)](https://docs.cloud.google.com/run/quotas), [IAP for Cloud Run](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run), [IAP GA blog](https://cloud.google.com/blog/products/serverless/iap-integration-with-cloud-run), [IAP custom OAuth clients](https://docs.cloud.google.com/iap/docs/custom-oauth-configuration), [Firebase pricing](https://firebase.google.com/pricing), [Disable billing with notifications](https://cloud.google.com/billing/docs/how-to/disable-billing-with-notifications)

Key takeaways:
- Free tier as of 2026-11-21, per billing account: Cloud Run request-based 180,000 vCPU-s, 360,000 GiB-s, 2 M requests/month; jobs and instance-based 240,000 vCPU-s, 450,000 GiB-s (tasks up to 24 h); GCS 5 GB-months only in us-east1/us-west1/us-central1; Cloud Build 2,500 min; Artifact Registry 0.5 GB; Secret Manager 6 versions, 10k accesses; Firebase Hosting 10 GB, 360 MB/day. Cloud Scheduler 3 jobs **(from memory, not re-checked)**.
- A billing account is required; budgets only alert. Hard cap pattern: budget -> Pub/Sub -> function that detaches billing.
- Shape: static public site (Firebase Hosting or public GCS bucket), one admin Cloud Run service (min instances 0), processing as a Cloud Run job started via the Run Admin API (`run.jobs.run`).
- Cloud Run caps HTTP/1 request bodies at 32 MiB: upload straight to GCS with a V4 signed URL or resumable session (bucket CORS, `signBlob` permission).
- Sign-in: IAP on Cloud Run is GA (2026) without a load balancer but covers the whole service and needs a custom OAuth client in org-less projects; app-level Google Identity Services with an owner-email check is simpler and composes with Drive access. Whether IAP accepts a Personal-mode client is **(unverified)**.
- A self-hosted box (e.g. Home Assistant app with ingress) can run the same container and push the static site; a hybrid (box exports base data, cloud does the rest) is natural.

**Relevance**: a near-zero-cost hosting shape for a processing pipeline plus public results and owner-only admin.

---

## Google Drive as a data store

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [Manage app audience](https://support.google.com/cloud/answer/15549945?hl=en), [Personal mode quick setup](https://support.google.com/cloud/answer/15544987#personal-mode-onboarding), [Drive API scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth), [Picker guide](https://developers.google.com/drive/picker/guides/overview), [Picker folder contents not granted (community)](https://groups.google.com/g/google-apps-script-community/c/_W-NKbttfbo), [Service-account storage quota](https://discuss.google.dev/t/error-403-storagequotaexceeded-when-the-service-accounts-drive-is-completely-empty/194265)

Key takeaways:
- OAuth apps in "Testing" status: refresh tokens expire 7 days after consent (beyond openid/email/profile), unusable for unattended jobs.
- Personal mode (Google Auth Platform): only the creating account can sign in; no verification, no 100-user cap, no 7-day expiry. Chosen once per project and cannot be changed. Workspace accounts may still need admin approval for restricted scopes.
- `drive.file` + Picker is non-sensitive (no verification) but grants per-file access; picking a folder does not grant existing contents (community reports). Use `setOptOutIncludingGrantedScopes(true)`.
- Service accounts created after 2025-04-15 have no Drive quota: they can read a shared folder but writes fail with `storageQuotaExceeded`; shared drives need Workspace.
- Pattern: Drive as inbox and archive, GCS as working store, refresh token in Secret Manager.

**Relevance**: how a self-deployed app can read a user's existing Drive data without verification or expiring tokens.

---

## Deploy-your-own packaging

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [cloud-run-button](https://github.com/GoogleCloudPlatform/cloud-run-button), [Deploy Cloud Run from Cloud Shell](https://docs.cloud.google.com/shell/docs/deploy-cloud-run-app), [Cloud Run IAP via Terraform](https://docs.cloud.google.com/run/docs/securing/identity-aware-proxy-cloud-run)

Key takeaways:
- Cloud Run Button (active, last push 2026-07): README badge deploys one service from the repo's Dockerfile; `app.json` prompts for env vars and runs pre/postcreate hooks **(hook capability from memory; check the README)**. Brittle for multi-resource setups.
- "Open in Cloud Shell" + idempotent `setup.sh` (and `teardown.sh`), optionally with a Cloud Shell tutorial: lowest concept count. OAuth client / Personal-mode setup and IAP's first enable are console-only, so the script must stop and say what to click.
- Terraform (`google_cloud_run_v2_service`/`_job`, buckets, `google_billing_budget`) is best for updates and teardown but needs state; offer as a second path.
- Publish images to GHCR via GitHub Actions and pull through an Artifact Registry remote repository so users build nothing **(verify remote-repo pull from GHCR)**.
- No strong GCP-specific exemplar found; borrow "one config file, one command, idempotent upgrade" from self-hosted apps such as Immich and Paperless-ngx.

**Relevance**: a path for other people to run their own copy of the tools in their own cloud project.

---

## Open-source survey and map tools worth reusing

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: none linked in the 2026-11-21 survey (project docs not re-fetched; add links on the next refresh)

Key takeaways:
- QFieldCloud and Mergin Maps self-host need a server stack (Django/PostGIS/MinIO) and overlap a custom logger; QField itself is useful alongside.
- ODK Central / KoboToolbox: form-based, wrong shape for GNSS sessions. GeoNode / Lizmap / QGIS Server: always-on server, overkill for static results.
- qgis2web exports a QGIS project to a static OpenLayers/Leaflet site; custom/local CRS support limited **(unverified)**.
- OpenLayers: a custom `ol/proj/Projection` in metres with no basemap is the best fit for local 2D coordinates. Leaflet `L.CRS.Simple`: light, phone-friendly flat plane.
- MapLibre GL JS is Web Mercator only (local metres must be faked as lon/lat near 0,0). deck.gl `OrthographicView` / `COORDINATE_SYSTEM.CARTESIAN` suits 3D surface, uncertainty and point-cloud views.
- Photo timeline: small enough to build (e.g. PhotoSwipe plus a time slider).

**Relevance**: favours a thin static viewer (OpenLayers 2D, deck.gl 3D) over adopting a server GIS.

---

## Responsive phone/desk design for a map app

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [Web Share Target (Chrome)](https://developer.chrome.com/docs/capabilities/web-apis/web-share-target), [caniuse share_target](https://caniuse.com/mdn-manifests_webapp_share_target), [GCS resumable uploads](https://cloud.google.com/storage/docs/resumable-uploads), [GCS signed URLs](https://cloud.google.com/storage/docs/access-control/signed-urls)

Key takeaways:
- Layout: full-bleed map; on phones a bottom sheet with peek/half/full snap points; at >= 900 px a side panel plus optional second panel. Same components, one breakpoint or container queries.
- PWA: manifest plus service worker for an offline shell; offline upload queue in IndexedDB with retry on `online`/`visibilitychange`. Background Sync is Chromium-only; iOS has no Web Share Target or Background Sync and may evict storage.
- Camera: `<input type="file" accept="image/*" capture="environment">` works on Android and iOS; zips via `accept=".zip,application/zip"`.
- Web Share Target (`share_target`, POST, multipart, files with MIME and extension) works only from an installed PWA on Chrome for Android.
- Large uploads go direct to GCS (resumable session or V4 signed PUT) because of Cloud Run's 32 MiB HTTP/1 limit; a native app can do the same with retry, more reliably than a share sheet.

**Relevance**: one web UI for field (phone) and desk use, and a reliable upload path from the field.

---

## AI APIs for photo notes and summaries

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [Gemini API terms](https://ai.google.dev/gemini-api/terms), [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing), [Free program ($300 not for Gemini API)](https://cloud.google.com/free/docs/free-cloud-features), [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing), [Secret Manager pricing](https://cloud.google.com/secret-manager/pricing)

Key takeaways:
- Gemini API free tier (Flash, Flash-Lite): content may be used to improve Google products and read by human reviewers; terms say not to submit sensitive or personal information. Paid tier: no training, limited abuse logging.
- Gemini paid prices per 1M tokens (2026-11-21): 3.5 Flash-Lite $0.30 in / $2.50 out; 3.8 Flash $0.75 / $3.75 through 2026-12-31, then $1.50 / $7.50. `gemini-2.5-flash` routes to 3.5 Flash-Lite from 2026-12-01. The $300 trial credit cannot pay for AI Studio Gemini usage.
- Gemini on Vertex AI: no API key (service account auth), Cloud terms (no training); prices similar **(not re-checked)**.
- Claude API: `claude-haiku-5-5` $0.10 in / $0.50 out per 1M tokens, handles images, no training by default under commercial terms; no ongoing free tier **(from memory)**.
- At ~50 calls a month (~150k tokens) any paid option costs under $0.05/month.
- Practice: key in Secret Manager, never in the browser; pluggable provider defaulting to none; strip EXIF/GPS and downscale before any AI call; store AI text as suggestions the owner accepts.

**Relevance**: optional AI notes at negligible cost, without sending personal photos to a training-eligible tier.

---

## Location privacy when publishing local-frame results

**Added**: 2026-11-21 | **Last verified**: 2026-11-21 | **Sources**: [3DEP on AWS](https://registry.opendata.aws/usgs-lidar/), [TNM Access API](https://tnmaccess.nationalmap.gov/api/v1/docs), [NGS web services](https://geodesy.noaa.gov/web_services/)

Key takeaways:
- Local coordinates alone do not anonymise: a parcel polygon, LiDAR- or DSM-derived surface, or building footprint can be matched by shape against public statewide parcel and LiDAR layers (public, bbox-queryable).
- A publish filter should drop parcel, imagery and LiDAR/DSM-derived layers, building and obstacle footprints, and absolute heights (offset to the arbitrary local datum).
- A GeoPackage SRS definition can hold the local frame's origin: strip or replace it before publishing.
- Re-encode photos to remove EXIF, XMP and maker notes, before publishing and before any AI call, and check them for street-facing content.
- A measured surface of a small feature on its own (e.g. an ice sheet) is low risk.
- Queries to public data APIs reveal the area: keep them out of public CI logs and issues.

**Relevance**: what a public results page must filter beyond coordinates and EXIF.
