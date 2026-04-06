FROM builder AS build

ARG MACHINE_NAME
ARG UBOOT_REPO
ARG UBOOT_REV
ARG UBOOT_DEFCONFIG

WORKDIR /work

# clone u-boot
RUN git init u-boot && \
    cd u-boot && \
    git remote add origin $UBOOT_REPO && \
    git fetch --depth=1 origin $UBOOT_REV && \
    git checkout FETCH_HEAD

RUN echo $MACHINE_NAME

# configs
COPY files/$MACHINE_NAME/*.config u-boot/defconfig/
COPY files/$MACHINE_NAME/boot.cmd.in u-boot/boot.cmd

# build
RUN cd u-boot && \
    export CROSS_COMPILE=aarch64-linux-gnu- && \
    make -j$(nproc) $UBOOT_DEFCONFIG && \
    scripts/kconfig/merge_config.sh -m .config defconfig/*.config && \
    make -j$(nproc) olddefconfig && \
    make -j$(nproc)

# mkimage
RUN cd u-boot && \
    tools/mkimage -A arm64 -T script -C none -n "Boot script" -d boot.cmd boot.scr

# archive
RUN cd u-boot && \
    mkdir -p install && \
    cp -v u-boot.bin install && \
    cp -v boot.scr install && \
    U_VER=$(grep "^VERSION =" Makefile | awk '{print $3}') && \
    U_PATCH=$(grep "^PATCHLEVEL =" Makefile | awk '{print $3}') && \
    UBOOT_VER=${U_VER}.${U_PATCH} && \
    tar cf uboot-artifacts.tar -C install .

# export stage
FROM scratch AS export
COPY --from=build /work/u-boot/uboot-artifacts.tar /