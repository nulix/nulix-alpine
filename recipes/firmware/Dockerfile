FROM builder AS build

ARG MACHINE_NAME
ARG FIRMWARE_REPO
ARG FIRMWARE_REV

WORKDIR /work

RUN git init firmware && \
    cd firmware && \
    git remote add origin $FIRMWARE_REPO && \
    git fetch --depth=1 origin $FIRMWARE_REV && \
    git checkout FETCH_HEAD

RUN mkdir -p install && \
    cp firmware/boot/bootcode.bin install && \
    cp firmware/boot/fixup*.dat install && \
    cp firmware/boot/start*.elf install

COPY files/$MACHINE_NAME/config.txt install/
COPY files/$MACHINE_NAME/cmdline.txt install/

RUN tar cf /firmware-artifacts.tar -C install .

# export stage
FROM scratch AS export
COPY --from=build /firmware-artifacts.tar /