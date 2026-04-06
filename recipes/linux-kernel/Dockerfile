FROM builder AS build

ARG MACHINE_ARCH
ARG KERNEL_REPO
ARG KERNEL_REV
ARG KERNEL_DEFCONFIG
ARG KERNEL_IMAGE

WORKDIR /work

# clone kernel
RUN git init linux && \
    cd linux && \
    git remote add origin $KERNEL_REPO && \
    git fetch --depth=1 origin $KERNEL_REV && \
    git checkout FETCH_HEAD

# build kernel
RUN cd linux && \
    export ARCH=arm64 && \
    export CROSS_COMPILE=aarch64-linux-gnu- && \
    make O=build -j$(nproc) $KERNEL_DEFCONFIG && \
    make O=build -j$(nproc) $KERNEL_IMAGE modules dtbs && \
    make O=build INSTALL_MOD_STRIP=1 INSTALL_MOD_PATH=modules modules_install

# prepare artifacts
RUN cd linux && \
    rm -r build/modules/lib/modules/*/build && \
    mkdir -p install/overlays && \
    cp build/arch/${MACHINE_ARCH}/boot/dts/broadcom/*.dtb install && \
    cp build/arch/${MACHINE_ARCH}/boot/dts/overlays/*.dtb* install/overlays && \
    cp arch/${MACHINE_ARCH}/boot/dts/overlays/README install/overlays && \
    cp build/arch/${MACHINE_ARCH}/boot/${KERNEL_IMAGE} install

# archive
RUN cd linux && \
    K_VER=$(grep "^VERSION =" Makefile | cut -d ' ' -f 3) && \
    K_PATCH=$(grep "^PATCHLEVEL =" Makefile | cut -d ' ' -f 3) && \
    K_SUB=$(grep "^SUBLEVEL =" Makefile | cut -d ' ' -f 3) && \
    KERNEL_VER=${K_VER}.${K_PATCH}.${K_SUB} && \
    echo $KERNEL_VER > version && \
    tar cf kernel-modules.tar -C build/modules/lib/modules . && \
    tar cf kernel-artifacts.tar -C install .

# export stage
FROM scratch AS export
COPY --from=build /work/linux/kernel-modules.tar /work/linux/kernel-artifacts.tar /work/linux/version /