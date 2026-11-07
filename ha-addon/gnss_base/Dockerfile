# Two stages: the first builds RTKLIB-EX's rtkrcv from source (rtklibexplorer's RTKLIB,
# the demo5 fork; Ubuntu's package is RTKLIB 2.4.3 b34, which barely resolves
# ambiguities on a 25 km baseline), the second is the runtime image, with the one
# binary copied in as rtkrcv-ex. Both rtkrcv instances of the app (measured position,
# reference check) run it. The tag's commit is pinned and checked, so the image is the
# same wherever it is built (Home Assistant builds it on the device: arm64 or amd64).
# Plain C, no Fortran: rtkrcv's makefile links only libm and libpthread.
ARG BUILD_FROM=ubuntu:24.04

FROM ${BUILD_FROM} AS rtklib
ARG RTKLIB_REPO=https://github.com/rtklibexplorer/RTKLIB.git
ARG RTKLIB_TAG=v2.5.1
ARG RTKLIB_COMMIT=62d4677ed8425a4e2748c6d390b500d1afb493fc
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git gcc make libc6-dev \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --quiet --depth 1 --branch "${RTKLIB_TAG}" "${RTKLIB_REPO}" /rtklib \
 && test "$(git -C /rtklib rev-parse HEAD)" = "${RTKLIB_COMMIT}" \
 && make -C /rtklib/app/consapp/rtkrcv/gcc -j"$(nproc)" \
 && /rtklib/app/consapp/rtkrcv/gcc/rtkrcv --version 2>&1 | grep -q "2.5.1"

FROM ${BUILD_FROM}
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-aiohttp python3-serial \
 && rm -rf /var/lib/apt/lists/*
COPY --from=rtklib /rtklib/app/consapp/rtkrcv/gcc/rtkrcv /usr/local/bin/rtkrcv-ex
COPY gnss_base /app/gnss_base
WORKDIR /app
CMD [ "python3", "-m", "gnss_base" ]
