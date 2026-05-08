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
ARG OTA_SYSROOT="/sysroot"
ARG OSTREE_REPO="repo"
ARG OSTREE_OSNAME="nulix"
ARG OSTREE_BRANCHNAME="stable"
ARG OSTREE_MANIFEST="manifest"
ARG IMAGE_FILE="nulix-os.img"

# ------------------------
# Init OSTree sysroot
# ------------------------
RUN mkdir -p ${OTA_SYSROOT} && \
    ostree admin --sysroot=${OTA_SYSROOT} init-fs --modern ${OTA_SYSROOT} && \
    ostree admin --sysroot=${OTA_SYSROOT} os-init ${OSTREE_OSNAME}

# Preparation required to steer ostree bootloader detection
RUN mkdir -p ${OTA_SYSROOT}/boot/loader.0 && \
    ln -s loader.0 ${OTA_SYSROOT}/boot/loader && \
    touch ${OTA_SYSROOT}/boot/loader/uEnv.txt

# Apply generic configurations to the deployed repository; they are
# specified as a series of "key:value ..." pairs.
RUN for cfg in ${OSTREE_OTA_REPO_CONFIG}; do \
        ostree config --repo=${OTA_SYSROOT}/ostree/repo set \
            "$(echo "${cfg}" | cut -d ":" -f1)" \
            "$(echo "${cfg}" | cut -d ":" -f2-)"; \
    done

# Use OSTree hash to avoid any potential race conditions between
# multiple builds accessing the same ${OSTREE_REPO}.
RUN ostree_target_hash=$(cat ${OSTREE_MANIFEST}) && \
    ostree --repo=${OTA_SYSROOT}/ostree/repo pull-local --remote=${OSTREE_OSNAME} ${OSTREE_REPO} ${ostree_target_hash} && \
    kargs_list="" && \
    for arg in $(printf '%s' "${OSTREE_KERNEL_ARGS}"); do \
        kargs_list="${kargs_list} --karg-append=${arg}"; \
    done

# Create the same reference on the device we use in the archive OSTree
# repo in ${OSTREE_REPO}. This reference will show up when showing the
# deployment on the device:
# ostree admin status
# If a remote with the name ${OSTREE_OSNAME} is configured, this also
# will allow to use:
# ostree admin upgrade
RUN ostree_target_hash=$(cat ${OSTREE_MANIFEST}) && \
    ostree --repo=${OTA_SYSROOT}/ostree/repo refs --create=${OSTREE_OSNAME}:${OSTREE_BRANCHNAME} ${ostree_target_hash} && \
    ostree admin --sysroot=${OTA_SYSROOT} deploy ${kargs_list} --os=${OSTREE_OSNAME} ${OSTREE_OSNAME}:${OSTREE_BRANCHNAME}

RUN if [ "${OSTREE_SYSROOT_READONLY}" = "1" ]; then \
        ostree config --repo=${OTA_SYSROOT}/ostree/repo set sysroot.readonly true; \
    fi

# Create /var/sota if it doesn't exist yet
RUN mkdir -p ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/sota
# Ensure the permissions are correctly set
RUN chmod 700 ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/sota

RUN mkdir -p ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/rootdirs

RUN mkdir -p ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/cache

# Ensure that /var/local exists (AGL symlinks /usr/local to /var/local)
RUN install -d ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/local

# Set package version for the first deployment
RUN ostree_target_hash=$(cat ${OSTREE_MANIFEST}) && \
    target_version=${ostree_target_hash} && \
    mkdir -p ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/sota/import && \
    echo "{\"${ostree_target_hash}\":\"${GARAGE_TARGET_NAME}-${target_version}\"}" \
        > ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/sota/import/installed_versions

# ------------------------
# Prepare image
# ------------------------
RUN dd if=/dev/zero of=${IMAGE_FILE} bs=1M count=1024 && \
    parted -s ${IMAGE_FILE} mklabel msdos \
        mkpart primary fat32 1MiB 100MiB \
        mkpart primary ext4 100MiB 100% \
        print

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
    mkfs ext4 /dev/sda2 label:otaroot inode:4096

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
# Write raw boot files to image
# ------------------------
RUN if [ -n "${UBOOT_IMAGES}" ]; then \
      tar -xvf ${BOOT_FILES_ARCHIVE}; \
      for i in ${UBOOT_IMAGES}; do \
        img=$(echo "$i" | cut -d'@' -f1); \
        offset=$(echo "$i" | cut -d'@' -f2); \
        seek_kblocks=$(( (offset) / 2 )); \
        dd if=${img} of=${IMAGE_FILE} bs=1K seek=${seek_kblocks} conv=notrunc && sync; \
      done; \
    fi

# ------------------------
# Compress
# ------------------------
RUN bzip2 ${IMAGE_FILE}

# ------------------------
# Export
# ------------------------
FROM scratch AS export
COPY --from=build /work/*.bz2 /
