# ------------------------
# Base builder
# ------------------------
FROM alpine:3.22.2 AS rootfs

ARG MACHINE_NAME
ARG MACHINE_ARCH
ARG MACHINE_VIRT
ARG KERNEL_IMAGE
ARG SERIAL_PORT
ARG SERIAL_BAUD
ARG BOOT_DEV_PART
ARG UBOOT_ENV_SIZE
ARG PACKAGES=""
ARG SSH_PUBLIC_KEY=""
ARG OSTREE_BRANCHNAME="nulix/stable"
ARG DISTRO_VERSION="unknown"

ENV ROOTFS="/rootfs"

# ------------------------
# Bring in artifacts from other stages
# ------------------------
COPY --from=linux-kernel /kernel-artifacts.tar .
COPY --from=linux-kernel /kernel-modules.tar .
COPY --from=linux-kernel /version .
COPY --from=ostree-initramfs /initramfs.img .

# ------------------------
# Prepare rootfs
# ------------------------
RUN mkdir -p $ROOTFS

RUN mkdir -p $ROOTFS/etc/apk && \
    cp /etc/apk/repositories $ROOTFS/etc/apk/repositories && \
    cp -r /etc/apk/keys $ROOTFS/etc/apk/

# install base system directly into rootfs
RUN apk add --no-cache \
    --root $ROOTFS \
    --initdb \
    busybox ostree util-linux \
    $(echo "$PACKAGES" | tr ',' ' ') \
    $( \
        if [ "$MACHINE_VIRT" = "docker" ]; then \
            echo "docker-engine docker-cli docker-openrc docker-compose"; \
        elif [ "$MACHINE_VIRT" = "lxc" ]; then \
            echo "lxc lxc-openrc"; \
        elif [ "$MACHINE_VIRT" = "podman" ]; then \
            echo "podman podman-openrc"; \
        fi \
    )

# ------------------------
# Install kernel, modules and initramfs
# ------------------------
RUN mkdir -p kernel-modules kernel-artifacts && \
    tar xf kernel-modules.tar -C kernel-modules && \
    tar xf kernel-artifacts.tar -C kernel-artifacts

RUN K_VER=$(ls kernel-modules) && \
    DEST="$ROOTFS/usr/lib/modules/$K_VER" && \
    mkdir -p $ROOTFS/usr/lib/modules && \
    cp -rv kernel-modules/$K_VER $ROOTFS/usr/lib/modules && \
    cp -v kernel-artifacts/$KERNEL_IMAGE $DEST/vmlinuz && \
    cp -v initramfs.img $DEST/initramfs.img

# ------------------------
# openrc setup
# ------------------------
# copy custom initd scripts
COPY openrc/tmpfiles.initd $ROOTFS/etc/init.d/tmpfiles
COPY openrc/resize-rootfs.initd $ROOTFS/etc/init.d/resize-rootfs
COPY openrc/touch-ostree.initd $ROOTFS/etc/init.d/touch-ostree
COPY openrc/portainer.initd $ROOTFS/etc/init.d/portainer

# make initd scripts executable
RUN chmod +x $ROOTFS/etc/init.d/tmpfiles && \
    chmod +x $ROOTFS/etc/init.d/resize-rootfs && \
    chmod +x $ROOTFS/etc/init.d/touch-ostree && \
    chmod +x $ROOTFS/etc/init.d/portainer

# configure console
RUN cd $ROOTFS/etc/conf.d && \
    cp agetty agetty.$SERIAL_PORT && \
    sed -i "s/^#baud=\"\"/baud=\"$SERIAL_BAUD\"/" agetty.$SERIAL_PORT && \
    cd $ROOTFS/etc/init.d && \
    ln -s agetty agetty.$SERIAL_PORT

# configure networking
RUN mkdir -p $ROOTFS/etc/network && \
    printf "auto lo\niface lo inet loopback\n\n\
auto eth0\niface eth0 inet dhcp\n" \
    > $ROOTFS/etc/network/interfaces

RUN set -eux; \
    \
    # create runlevel dirs
    for level in sysinit boot shutdown default; do \
        mkdir -p "$ROOTFS/etc/runlevels/$level"; \
    done; \
    \
    # sysinit
    ln -sf /etc/init.d/devfs "$ROOTFS/etc/runlevels/sysinit/devfs"; \
    ln -sf /etc/init.d/dmesg "$ROOTFS/etc/runlevels/sysinit/dmesg"; \
    ln -sf /etc/init.d/mdev "$ROOTFS/etc/runlevels/sysinit/mdev"; \
    ln -sf /etc/init.d/tmpfiles "$ROOTFS/etc/runlevels/sysinit/tmpfiles"; \
    ln -sf /etc/init.d/resize-rootfs "$ROOTFS/etc/runlevels/sysinit/resize-rootfs"; \
    \
    # boot
    ln -sf /etc/init.d/hwclock "$ROOTFS/etc/runlevels/boot/hwclock"; \
    ln -sf /etc/init.d/modules "$ROOTFS/etc/runlevels/boot/modules"; \
    ln -sf /etc/init.d/sysctl  "$ROOTFS/etc/runlevels/boot/sysctl"; \
    ln -sf /etc/init.d/hostname "$ROOTFS/etc/runlevels/boot/hostname"; \
    ln -sf /etc/init.d/bootmisc "$ROOTFS/etc/runlevels/boot/bootmisc"; \
    ln -sf /etc/init.d/syslog  "$ROOTFS/etc/runlevels/boot/syslog"; \
    \
    # shutdown
    ln -sf /etc/init.d/mount-ro "$ROOTFS/etc/runlevels/shutdown/mount-ro"; \
    ln -sf /etc/init.d/killprocs "$ROOTFS/etc/runlevels/shutdown/killprocs"; \
    ln -sf /etc/init.d/savecache "$ROOTFS/etc/runlevels/shutdown/savecache"; \
    \
    # default
    ln -sf /etc/init.d/firstboot "$ROOTFS/etc/runlevels/default/firstboot"; \
    ln -sf /etc/init.d/touch-ostree "$ROOTFS/etc/runlevels/default/touch-ostree"; \
    ln -sf /etc/init.d/sshd "$ROOTFS/etc/runlevels/default/sshd"; \
    ln -sf /etc/init.d/agetty.${SERIAL_PORT} "$ROOTFS/etc/runlevels/default/agetty.${SERIAL_PORT}"; \
    ln -sf /etc/init.d/chronyd "$ROOTFS/etc/runlevels/default/chronyd"; \
    \
    # docker
    if [ "$MACHINE_VIRT" = "docker" ]; then \
        mkdir -p $ROOTFS/var/lib/docker && \
        ln -sf /etc/init.d/docker "$ROOTFS/etc/runlevels/default/docker" && \
        ln -sf /etc/init.d/portainer "$ROOTFS/etc/runlevels/default/portainer"; \
    fi

# install openrc-init reboot and poweroff scripts
COPY openrc/poweroff.in $ROOTFS/sbin/
COPY openrc/reboot.in $ROOTFS/sbin/
RUN mv $ROOTFS/sbin/poweroff.in $ROOTFS/sbin/poweroff && \
    mv $ROOTFS/sbin/reboot.in $ROOTFS/sbin/reboot && \
    chmod +x $ROOTFS/sbin/poweroff && \
    chmod +x $ROOTFS/sbin/reboot

# ------------------------
# Link init
# ------------------------
RUN cd $ROOTFS/sbin && \
    ln -sf openrc-init init

# openrc-init does not use /etc/inittab
RUN rm $ROOTFS/etc/inittab

# ------------------------
# Basic system config
# ------------------------
# set root password
RUN echo "root:root" | chroot $ROOTFS chpasswd

# add login console
RUN grep -q "$SERIAL_PORT" $ROOTFS/etc/securetty || echo "$SERIAL_PORT" >> $ROOTFS/etc/securetty

# cleanups
RUN printf "" > $ROOTFS/etc/motd
RUN printf "" > $ROOTFS/etc/fstab
COPY files/issue.in $ROOTFS/etc/issue
COPY files/setup_env.sh $ROOTFS/etc/profile.d/
COPY files/panic.conf $ROOTFS/etc/sysctl.d/99-panic.conf

# SSH setup
RUN if [ -n "$SSH_PUBLIC_KEY" ]; then \
    mkdir -p $ROOTFS/root/.ssh && \
    echo "$SSH_PUBLIC_KEY" > $ROOTFS/root/.ssh/authorized_keys && \
    chmod 600 $ROOTFS/root/.ssh/authorized_keys; \
fi

# package manifest
RUN mkdir -p /out  && \
    apk --root $ROOTFS info -vv > /out/package.manifest

# ------------------------
# Machine specific files
# ------------------------
COPY machine/fstab.in $ROOTFS/etc/fstab
COPY machine/fw_env.config.in $ROOTFS/etc/fw_env.config
RUN sed -i "s/@@BOOT_DEV@@/${BOOT_DEV_PART}/" $ROOTFS/etc/fstab
RUN sed -i "s/@@ENV_SIZE@@/${UBOOT_ENV_SIZE}/" $ROOTFS/etc/fw_env.config

# ----------------------
# usrmerge
# ----------------------
RUN apk add --no-cache ostree patchelf

RUN mv $ROOTFS/bin/* $ROOTFS/usr/bin 2>/dev/null || true && \
    $ROOTFS/usr/bin/busybox rm -v -rf $ROOTFS/bin && \
    $ROOTFS/usr/bin/busybox ln -v -s usr/bin $ROOTFS/bin && \
    mkdir -v -p $ROOTFS/usr/sbin && \
    mv -v $ROOTFS/sbin/* $ROOTFS/usr/sbin && \
    rm -v -rf $ROOTFS/sbin && \
    ln -s usr/sbin $ROOTFS/sbin && \
    mkdir -v -p $ROOTFS/usr/lib && \
    cp -v -a $ROOTFS/lib/* $ROOTFS/usr/lib && \
    cp -v $ROOTFS/usr/bin/busybox $ROOTFS/tmp && \
    patchelf --set-interpreter $ROOTFS/usr/lib/ld-musl-*.so.* $ROOTFS/tmp/busybox && \
    rm -v -rf $ROOTFS/lib && \
    $ROOTFS/tmp/busybox ln -v -s usr/lib $ROOTFS/lib && \
    \
    if [ -d "$ROOTFS/lib64" ]; then \
        mkdir -p $ROOTFS/usr/lib64 \
        cp -a $ROOTFS/lib64/* $ROOTFS/usr/lib64/ \
        rm -rf $ROOTFS/lib64 \
        ln -s usr/lib64 $ROOTFS/lib64; \
    fi && \
    rm -v -f $ROOTFS/tmp/busybox

RUN mkdir -p $ROOTFS/sysroot && \
    ln -v -sf /sysroot/ostree $ROOTFS/ostree && \
    mkdir -p $ROOTFS/usr/rootdirs && \
    mkdir -p $ROOTFS/usr/etc && \
    cp -a $ROOTFS/etc/. $ROOTFS/usr/etc/ && \
    echo -n "${MACHINE_NAME}" > $ROOTFS/usr/etc/hostname && \
    ln -v -sf /run/resolv.conf $ROOTFS/usr/etc/resolv.conf && \
    mkdir -v -p $ROOTFS/usr/share/sota && \
    echo -n "${OSTREE_BRANCHNAME}" > $ROOTFS/usr/share/sota/branchname && \
    rm -rf $ROOTFS/etc

# ----------------------
# Rootdirs layout
# ----------------------
RUN mkdir -p "$ROOTFS/var/rootdirs" && \
    \
    # move /home
    if [ -d "$ROOTFS/home" ]; then \
        mv "$ROOTFS/home" "$ROOTFS/var/rootdirs/"; \
    fi && \
    ln -sf /var/rootdirs/home "$ROOTFS/home" && \
    \
    # standard dirs
    for dir in media mnt opt srv; do \
        rm -rf "$ROOTFS/$dir" && \
        ln -sf "/var/rootdirs/$dir" "$ROOTFS/$dir"; \
    done && \
    \
    # root home
    if [ -d "$ROOTFS/root" ]; then \
        mv "$ROOTFS/root" "$ROOTFS/var/roothome"; \
    fi && \
    ln -sf /var/roothome "$ROOTFS/root"

# ----------------------
# usr/local handling
# ----------------------
RUN rm -v -rf $ROOTFS/usr/local && \
    ln -v -sf ../var/usrlocal $ROOTFS/usr/local

# ------------------------
# OSTree commit
# ------------------------
RUN rm -rf $ROOTFS/dev $ROOTFS/proc $ROOTFS/sys $ROOTFS/run && \
    mkdir -p $ROOTFS/dev $ROOTFS/proc $ROOTFS/sys $ROOTFS/run
RUN mkdir -p /out/ostree-repo && \
    ostree --repo=/out/ostree-repo init --mode=archive-z2 && \
    commit_hash=$(ostree --repo=/out/ostree-repo commit \
        --tree=dir=$ROOTFS \
        --branch=$OSTREE_BRANCHNAME \
        --subject="NULIX OS" \
        --body="Version $DISTRO_VERSION" \
        --skip-if-unchanged) && \
    echo "$commit_hash" > /out/ostree.commit && \
    ostree --repo=/out/ostree-repo summary -u

# ------------------------
# Final artifacts
# ------------------------
RUN tar czf /out/rootfs.tar.gz -C $ROOTFS . && \
    tar czf /out/ostree-repo.tar.gz -C /out ostree-repo

# ------------------------
# Export stage
# ------------------------
FROM scratch AS export
COPY --from=rootfs /out /
