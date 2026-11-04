FROM ubuntu:24.04
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-aiohttp python3-serial rtklib \
 && rm -rf /var/lib/apt/lists/*
COPY gnss_base /app/gnss_base
WORKDIR /app
CMD [ "python3", "-m", "gnss_base" ]
