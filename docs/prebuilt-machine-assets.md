# Prebuilt foundation machine assets

NULIX publishes four scratch OCI images under `ghcr.io/nulix/nulix-alpine/`:

- `raspberry-pi-3-linux-kernel`: kernel image, matching modules, DTBs and overlays.
- `raspberry-pi-3-u-boot`: U-Boot binary and boot script.
- `raspberry-pi-3-firmware`: Raspberry Pi boot firmware and configuration.
- `raspberry-pi-3-ostree-initramfs`: the OSTree boot environment.

These contain build inputs, not runnable application containers. No customer profile,
SSH key, enrollment credential, agent customization or rootfs is published here.
The agent remains part of the rootfs recipe. This does not introduce APK packages.

## First publication

1. Commit and push the publishing workflow, script and tests to the OS repository.
   The `Publish foundation machine assets` workflow runs on relevant changes to main,
   or can be started from Actions manually. It compiles on a GitHub-hosted ARM64 runner,
   independently of the platform's single-slot Oracle builder.
2. The workflow signs in using its `GITHUB_TOKEN` with `packages: write`, publishes all
   four images, saves their immutable digests, and opens a PR containing
   `machine-assets.lock.json` and `hosted-machine-assets.hcl`. It needs the repository
   Actions setting **Allow GitHub Actions to create and approve pull requests**.
   If PR creation is disabled, download the `raspberry-pi-3-machine-assets` workflow
   artifact and commit both files together instead. Do not invent placeholder digests.
3. GHCR packages initially have private visibility, even for public repositories.
   Make these four foundation packages public in their package settings so local and
   Oracle builders can pull anonymously. Alternatively, authenticate the OS user that
   runs Buildx using a classic PAT with `read:packages`. The runner uses root; a login
   as `ubuntu` alone does not configure root's Docker credentials. Credentials belong
   on the build host, never in the repository, Bake file, rootfs or enrollment image.
4. Review and merge the pins. Run `Build image` against the merged revision and test
   the image on a Raspberry Pi 3 before publishing a GitHub foundation release.
   New platform builds must select that new foundation revision; existing foundation
   releases remain pinned to their original source and assets.
5. Upgrade the platform CLI and hosted runner with the matching platform changes.
   The runner verifies the component lock before applying user settings. There is no
   database migration and no automatic device deployment.

Publication uses registry build caches to preserve intermediate compiler work across
workflow runs. Do not delete image versions still referenced by foundation locks.
Cache tags may move; the component references consumed by OS builds always use digests.

## Subsequent builds

`nulix build --machine raspberry-pi-3 --version 1.2.8` automatically uses the pins
with the updated CLI. For direct Bake:

```sh
python3 scripts/machine-assets.py check
docker buildx bake -f docker-bake.hcl -f machines/raspberry-pi-3.hcl \
  -f hosted-machine-assets.hcl
```

The resolved graph contains only `nulix-builder`, `ostree-rootfs`, and
`ostree-sd-image`. It pulls machine artifacts, installs rootfs packages and the agent,
creates an OSTree commit, and assembles the flashable image. The builder tools image
can still build if its local cache is cold; it does not compile a kernel or U-Boot.
An unavailable GHCR image fails the build rather than silently compiling from source.

The lock fingerprints effective component Bake arguments, the shared builder recipe,
and component recipe files. A rootfs/agent-only change keeps the pins valid. Changing
kernel sources/configuration, bootloader, firmware, initramfs or builder inputs requires
rerunning publication and merging fresh pins. The publication workflow always uses the
source targets and ignores the consumption overlay.

To publish manually on a sufficiently provisioned ARM64 Docker host, first authenticate
to GHCR with `write:packages`, then run:

```sh
python3 scripts/machine-assets.py publish --repository nulix/nulix-alpine
```

Commit the resulting two pin files only after all four images publish successfully.
The fingerprint identifies recipe inputs; it does not make mutable upstream apt/apk
repositories reproducible. Published digests preserve the actual built artifact.

## Bootloader update boundary

New flashable images include the selected bootloader and firmware. Existing devices
receiving an OSTree update do not automatically replace their boot partition's U-Boot
or firmware. Keep foundation delivery marked `reflash` until that separate update path
is implemented and tested. Kernel modules and the kernel image are always consumed
together from one artifact digest.
