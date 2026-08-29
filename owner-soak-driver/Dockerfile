# zkdeal owner soak driver image (OWNER_SOAK_DRIVER_IMAGE).
#
# This image carries exactly one artifact: the source-bound owner soak driver
# at /opt/zkdeal-owner-soak, plus the MIT license. The soak-runner `candidate` target copies
# /opt/zkdeal-owner-soak out of this image by digest and fails closed unless
# the copied bytes match OWNER_SOAK_DRIVER_SOURCE_SHA256, which must also be
# the value of this image's org.zkdeal.owner-soak-driver.source.sha256 label.
#
# Build from the umbrella repository root so the root license file is in the
# build context:
#
#   OWNER_SOAK_DRIVER_SOURCE_SHA256="$(sha256sum \
#       cloud-deployer-infra/owner-soak-driver/zkdeal_owner_soak.py | awk '{print $1}')"
#   docker build \
#     -f cloud-deployer-infra/owner-soak-driver/Dockerfile \
#     --build-arg OWNER_SOAK_DRIVER_SOURCE_SHA256="$OWNER_SOAK_DRIVER_SOURCE_SHA256" \
#     -t zkdeal-owner-soak-driver .

FROM python:3.13-alpine@sha256:540c7d91f98ff6880174c40e99067bf5941eb54d818a7a5e094d188b196a934d AS check

COPY cloud-deployer-infra/owner-soak-driver/zkdeal_owner_soak.py /check/zkdeal-owner-soak
COPY LICENSE /check/LICENSE

ARG OWNER_SOAK_DRIVER_SOURCE_SHA256
RUN test -n "$OWNER_SOAK_DRIVER_SOURCE_SHA256" \
 && python -m py_compile /check/zkdeal-owner-soak \
 && actual="$(sha256sum /check/zkdeal-owner-soak | awk '{print $1}')" \
 && test "$actual" = "$OWNER_SOAK_DRIVER_SOURCE_SHA256"

# The published image is bare: no shell, no interpreter, nothing but the
# hash-bound driver bytes and the license. The candidate soak-runner stage
# provides the Python 3.13 runtime that executes /opt/zkdeal-owner-soak.
FROM scratch

ARG OWNER_SOAK_DRIVER_SOURCE_SHA256
COPY --from=check --chmod=0555 /check/zkdeal-owner-soak /opt/zkdeal-owner-soak
COPY --from=check --chmod=0444 /check/LICENSE /LICENSE

LABEL org.opencontainers.image.title="zkdeal-owner-soak-driver" \
      org.opencontainers.image.description="Hash-bound owner lifecycle driver for the 12-hour zkdeal release soak" \
      org.opencontainers.image.licenses="MIT" \
      org.zkdeal.owner-soak-driver.source.sha256="$OWNER_SOAK_DRIVER_SOURCE_SHA256"
