FROM alpine:3.22.2 AS initramfs-build

ARG MACHINE_ARCH

WORKDIR /work

# install build deps
RUN apk add --no-cache ostree u-boot-tools

# install init script
COPY files/init.sh initramfs/init
RUN chmod +x initramfs/init

# use busybox as a base for rootfs
RUN apk fetch --no-cache busybox && \
    tar xf busybox-*.apk -C initramfs && \
    rm busybox-*.apk && \
    cd initramfs/bin && \
    for i in $(busybox --list); do \
    ln -sf /bin/busybox $i; \
    done
RUN cd initramfs && \
    mkdir lib && \
    SH_LIBS=$(ldd bin/sh | grep '=>' | cut -d ' ' -f3) && \
    for i in $SH_LIBS; do \
    cp -v $i lib; \
    done

# install ostree in initramfs
RUN apk fetch --no-cache ostree && \
    tar xf ostree-*.apk -C initramfs && \
    rm ostree-*.apk && \
    cd initramfs && \
    OSTREE_LIBS=$(ldd usr/lib/ostree/ostree-prepare-root | grep '=>' | cut -d ' ' -f3) && \
    for i in $OSTREE_LIBS; do \
    cp -v $i lib; \
    done

# make cpio gz image
RUN cd initramfs && \
    find . | sort | cpio -o -H newc | gzip -9 > initramfs.img.gz

# make uboot image
RUN cd initramfs && \
    mkimage -A $MACHINE_ARCH -O linux -T ramdisk -C gzip \
    -n "OSTree initramfs" -d initramfs.img.gz initramfs.img

# export stage
FROM scratch AS export
COPY --from=initramfs-build /work/initramfs/initramfs.img /
