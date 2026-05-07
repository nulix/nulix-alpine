# nulix:deps=kernel,ostree-rootfs,u-boot,firmware

###
### INSPIRED WITH:
###
# https://github.com/uptane/meta-updater/blob/master/classes/image_types_ota.bbclass

###
### HOW TO USE THIS FILE:
###
# docker build -f Dockerfile.disk-image . -o .

###
### STAGE 1 - build
###
FROM builder AS build

WORKDIR /work

# ------------------------
# Import artifacts from Bake targets
# ------------------------
COPY --from=linux-kernel /kernel-artifacts.tar .
COPY --from=u-boot /uboot-artifacts.tar .
COPY --from=firmware /firmware-artifacts.tar .
COPY --from=ostree-rootfs /ostree-repo ./repo
COPY --from=ostree-rootfs /ostree.commit ./manifest

# ------------------------
# ENV (comes from bake args)
# ------------------------
ARG OTA_SYSROOT=/sysroot
ARG OSTREE_OSNAME=nulix
ARG OSTREE_BRANCHNAME=stable
ARG IMAGE_FILE=nulix-os.img

# ------------------------
# Init OSTree sysroot
# ------------------------
RUN mkdir -p ${OTA_SYSROOT} && \
    ostree admin --sysroot=${OTA_SYSROOT} init-fs --modern ${OTA_SYSROOT} && \
    ostree admin --sysroot=${OTA_SYSROOT} os-init ${OSTREE_OSNAME}

# bootloader prep
RUN mkdir -p ${OTA_SYSROOT}/boot/loader.0 && \
    ln -s loader.0 ${OTA_SYSROOT}/boot/loader && \
    touch ${OTA_SYSROOT}/boot/loader/uEnv.txt

# ------------------------
# Pull OSTree commit
# ------------------------
RUN ostree_target_hash=$(cat manifest) && \
    ostree --repo=${OTA_SYSROOT}/ostree/repo pull-local \
        --remote=${OSTREE_OSNAME} \
        repo ${ostree_target_hash}

# ------------------------
# Deploy system
# ------------------------
RUN ostree_target_hash=$(cat manifest) && \
    ostree --repo=${OTA_SYSROOT}/ostree/repo refs \
        --create=${OSTREE_OSNAME}:${OSTREE_BRANCHNAME} \
        ${ostree_target_hash} && \
    ostree admin --sysroot=${OTA_SYSROOT} deploy \
        --os=${OSTREE_OSNAME} \
        ${OSTREE_OSNAME}:${OSTREE_BRANCHNAME}

# ------------------------
# Prepare image
# ------------------------
RUN dd if=/dev/zero of=${IMAGE_FILE} bs=1M count=1024 && \
    parted -s ${IMAGE_FILE} mklabel msdos \
        mkpart primary fat32 1MiB 100MiB \
        mkpart primary ext4 100MiB 100%

# ------------------------
# Pack sysroot
# ------------------------
RUN tar cf ${OTA_SYSROOT}.tar -C ${OTA_SYSROOT} .

# ------------------------
# Populate partitions
# ------------------------
RUN guestfish -a ${IMAGE_FILE} <<EOF
run
mkfs vfat /dev/sda1 label:boot
mkfs ext4 /dev/sda2 label:otaroot

mount /dev/sda1 /
tar-in kernel-artifacts.tar /
tar-in uboot-artifacts.tar /
tar-in firmware-artifacts.tar /
umount /

mount /dev/sda2 /
tar-in ${OTA_SYSROOT}.tar /
ls /
umount /
EOF

# ------------------------
# Compress
# ------------------------
RUN bzip2 ${IMAGE_FILE}

# ------------------------
# Export
# ------------------------
FROM scratch AS export
COPY --from=build /work/*.bz2 /
