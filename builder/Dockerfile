FROM alpine:3.22.2

# ------------------------
# Metadata (optional but nice)
# ------------------------
LABEL org.opencontainers.image.title="nulix-builder"
LABEL org.opencontainers.image.description="Builder image for NULIX OS"
LABEL org.opencontainers.image.source="nulix"

# ------------------------
# Install build dependencies
# ------------------------
RUN apk add --no-cache \
    git \
    build-base \
    bc \
    bison \
    flex \
    openssl-dev \
    ncurses-dev \
    elfutils-dev \
    lzop \
    perl \
    linux-headers \
    util-linux-dev \
    gnutls-dev \
    findutils \
    bash \
    tar \
    xz \
    parted \
    e2fsprogs \
    dosfstools \
    util-linux \
    tar \
    kpartx \
    ostree \
    patchelf

# ------------------------
# Configure git
# ------------------------
RUN git config --global user.name "NULIX Builder" && \
    git config --global user.email "builder@nulix.io"

# ------------------------
# Workdir
# ------------------------
WORKDIR /work

# ------------------------
# Default shell (optional)
# ------------------------
SHELL ["/bin/sh", "-c"]