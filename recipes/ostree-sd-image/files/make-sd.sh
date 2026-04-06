#!/bin/sh
set -e

# ----------------------
# Debug: environment
# ----------------------

echo "========================================"
echo "🔧 NULIX BUILD ENVIRONMENT"
echo "========================================"

print_var() {
    name="$1"
    val="$(eval echo \"\${$name}\")"

    if [ -z "$val" ]; then
        printf "❌ %-25s = (EMPTY)\n" "$name"
    else
        printf "✅ %-25s = %s\n" "$name" "$val"
    fi
}

# core OSTree
print_var OTA_SYSROOT
print_var OSTREE_OSNAME
print_var OSTREE_REPO
print_var OSTREE_MANIFEST
print_var OSTREE_BRANCHNAME

# distro / version
print_var DISTRO_VERSION_MAJOR
print_var DISTRO_VERSION_MINOR
print_var DISTRO_VERSION_PATCH

# image
print_var IMAGE_FILE

# boot artifacts
print_var BOOT_FILES_ARCHIVE
print_var KERNEL_ARTIFACTS_ARCHIVE
print_var UBOOT_IMAGES

# optional configs
print_var OSTREE_KERNEL_ARGS
print_var OSTREE_OTA_REPO_CONFIG
print_var OSTREE_SYSROOT_READONLY

echo "========================================"

cp -v /outdir/kernel/kernel-artifacts.tar .
cp -v /outdir/u-boot/uboot-artifacts.tar .
cp -v /outdir/firmware/firmware-artifacts.tar .

# Init ostree sysroot files
mkdir -p ${OTA_SYSROOT}

ostree admin --sysroot=${OTA_SYSROOT} init-fs --modern ${OTA_SYSROOT}
ostree admin --sysroot=${OTA_SYSROOT} os-init ${OSTREE_OSNAME}

# Preparation required to steer ostree bootloader detection
mkdir -p ${OTA_SYSROOT}/boot/loader.0
ln -s loader.0 ${OTA_SYSROOT}/boot/loader
touch ${OTA_SYSROOT}/boot/loader/uEnv.txt

# Apply generic configurations to the deployed repository; they are
# specified as a series of "key:value ..." pairs.
for cfg in ${OSTREE_OTA_REPO_CONFIG}; do \
      ostree config --repo=${OTA_SYSROOT}/ostree/repo set \
           "$(echo "${cfg}" | cut -d ":" -f1)" \
           "$(echo "${cfg}" | cut -d ":" -f2-)"; \
done

# Use OSTree hash to avoid any potential race conditions between
# multiple builds accessing the same ${OSTREE_REPO}.
ostree_target_hash=$(cat ${OSTREE_MANIFEST})

ostree --repo=${OTA_SYSROOT}/ostree/repo pull-local --remote=${OSTREE_OSNAME} ${OSTREE_REPO} ${ostree_target_hash}

kargs_list=""
for arg in ${OSTREE_KERNEL_ARGS}; do
    kargs_list="${kargs_list} --karg-append=${arg}"
done

# deploy kernel


# Create the same reference on the device we use in the archive OSTree
# repo in ${OSTREE_REPO}. This reference will show up when showing the
# deployment on the device:
# ostree admin status
# If a remote with the name ${OSTREE_OSNAME} is configured, this also
# will allow to use:
# ostree admin upgrade
ostree_target_hash=$(cat ${OSTREE_MANIFEST})
ostree --repo=${OTA_SYSROOT}/ostree/repo refs --create=${OSTREE_OSNAME}:${OSTREE_BRANCHNAME} ${ostree_target_hash}
ostree config --repo=${OTA_SYSROOT}/ostree/repo set core.xattrs false
ostree config --repo=${OTA_SYSROOT}/ostree/repo set core.selinux false
ostree admin --sysroot=${OTA_SYSROOT} deploy \
    ${kargs_list} \
    --os=${OSTREE_OSNAME} \
    ${OSTREE_OSNAME}:${OSTREE_BRANCHNAME}

if [ "${OSTREE_SYSROOT_READONLY}" = "1" ]; then
    ostree config --repo=${OTA_SYSROOT}/ostree/repo set sysroot.readonly true;
fi

# Create /var/sota if it doesn't exist yet
mkdir -p ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/sota
# Ensure the permissions are correctly set
chmod 700 ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/sota

mkdir -p ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/rootdirs

mkdir -p ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/cache

# Ensure that /var/local exists (AGL symlinks /usr/local to /var/local)
install -d ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/local

# Set package version for the first deployment
ostree_target_hash=$(cat ${OSTREE_MANIFEST})
target_version=${ostree_target_hash}
mkdir -p ${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/sota/import

printf '{"%s":"%s-%s"}\n' \
    "$ostree_target_hash" \
    "$GARAGE_TARGET_NAME" \
    "$target_version" \
    > "${OTA_SYSROOT}/ostree/deploy/${OSTREE_OSNAME}/var/sota/import/installed_versions"

# blank image
dd if=/dev/zero of=${IMAGE_FILE} bs=1M count=1024

# prepare partitions
parted -s ${IMAGE_FILE} mklabel msdos \
      mkpart primary fat32 1MiB 100MiB \
      mkpart primary ext4 100MiB 100% \
      print

# prepare tar archives
#gzip -d uboot-artifacts.tar
#gzip -d kernel-artifacts.tar
tar cf ${OTA_SYSROOT}.tar -C ${OTA_SYSROOT} .

BOOT_FILES_ARCHIVE="uboot-artifacts.tar"
KERNEL_ARTIFACTS_ARCHIVE="kernel-artifacts.tar"
FIRMWARE_ARTIFACTS_ARCHIVE="firmware-artifacts.tar"

ls -la

# copy files to partitions
guestfish -a ${IMAGE_FILE} <<EOF
      run
      mkfs vfat /dev/sda1 label:boot
      mkfs ext4 /dev/sda2 label:otaroot inode:4096

      mount /dev/sda1 /
      tar-in ${BOOT_FILES_ARCHIVE} /
      tar-in ${KERNEL_ARTIFACTS_ARCHIVE} /
      tar-in ${FIRMWARE_ARTIFACTS_ARCHIVE} /
      echo "=== boot partition ==="
      ls /

      umount /
      mount /dev/sda2 /
      tar-in ${OTA_SYSROOT}.tar /
      echo "=== otaroot partition ==="
      ls /
      umount /

      exit
EOF

# write raw boot files to image
if [ -n "${UBOOT_IMAGES}" ]; then
    tar -xvf ${BOOT_FILES_ARCHIVE}

    for i in ${UBOOT_IMAGES}; do
        echo "Flashing"
        img=$(echo "$i" | cut -d'@' -f1)
        offset=$(echo "$i" | cut -d'@' -f2)
        seek_kblocks=$((offset / 2))

        dd if=${img} of=${IMAGE_FILE} bs=1K seek=${seek_kblocks} conv=notrunc
        sync
    done
fi

# compress the image
OS_VER=$DISTRO_VERSION_MAJOR.$DISTRO_VERSION_MINOR.$DISTRO_VERSION_PATCH && \
    mv ${IMAGE_FILE} ${IMAGE_FILE}-${OS_VER}.img && \
    IMAGE_FILE=${IMAGE_FILE}-${OS_VER}.img && \
    bzip2 ${IMAGE_FILE}

cp nulix-os-*.img.bz2 /out