FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV USER=root

RUN apt-get update && apt-get install -y --no-install-recommends \
    qemu-system-arm qemu-system-mips qemu-utils qemu-system-data \
    seabios vgabios ipxe-qemu \
    e2fsprogs multipath-tools util-linux \
    busybox-static \
    curl wget iputils-ping iproute2 net-tools bridge-utils uml-utilities socat \
    tar gzip cpio fdisk \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /bin/busybox /usr/bin/bash-static 2>/dev/null || true

RUN mkdir -p /work/binaries /work/scratch /work/images

WORKDIR /work