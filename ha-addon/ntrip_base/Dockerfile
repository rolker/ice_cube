FROM ubuntu:24.04
# RTKLIB 2.4.3 b34 from Ubuntu: str2str supports ntripc:// (NTRIP caster)
RUN apt-get update \
 && apt-get install -y --no-install-recommends rtklib jq \
 && rm -rf /var/lib/apt/lists/*
COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]
