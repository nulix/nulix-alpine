# Versioned machine components in GHCR

NULIX publishes a credential-free Rootfs Base and four hardware components. The
canonical package layout is:

```text
ghcr.io/<owner>/<repository>/<machine>/<component>/<release-family>
```

For the current Raspberry Pi 3 profile:

| Package under `ghcr.io/nulix/nulix-alpine/` | Full upstream/NULIX version |
| --- | --- |
| `raspberry-pi-3/rootfs-base/3.22` | Alpine 3.22.2 |
| `raspberry-pi-3/linux-kernel/6.12` | Linux 6.12.62 + matching modules and DTBs |
| `raspberry-pi-3/u-boot/2025.10` | U-Boot 2025.10 + boot script |
| `raspberry-pi-3/firmware/2025.12` | 2025-12-17 firmware snapshot `f1ea7092589b` |
| `raspberry-pi-3/ostree-initramfs/1.0` | NULIX OSTree initramfs 1.0.0 |

The `dev` publishing channel adds `-dev` to each family namespace, for example
`raspberry-pi-3/linux-kernel/6.12-dev`. These are separate GHCR packages, not tags
of the stable package. Each package has an `input-<publication-hash>` lookup tag
and a full-version/build tag such as `6.12.62-<hash-prefix>`. Consumers always pin
`@sha256:...`; neither tag is a substitute for a digest lock.

## Rootfs Base and OS customization

`recipes/rootfs-base/Dockerfile` supplies the Alpine userspace image used by the
rootfs recipe. `ALPINE_VERSION` is shared through Bake. The base does not contain
a device certificate, enrollment token, user's SSH key, Compose stack or user
profile. Rootfs/agent customization and the OSTree commit are assembled per OS
build. This is not a preconfigured user OS and does not introduce APK artifacts.
The other four packages are scratch build-input images, not application containers.

## Release metadata

`machines/raspberry-pi-3.components.json` records the full versions, release
families and pinned upstream revisions. Publication validates it against effective
Bake arguments: hardware source pins, board, architecture and Alpine version.
When updating a source revision, update its full version/family metadata as well.
For the NULIX-owned initramfs, bump its version when its boot behavior changes.
Firmware uses a date/source snapshot because upstream has no semantic release
number for this package. A build fingerprint distinguishes rebuilds of a source
version with changed recipes or tooling.

Inspect the release plan without building or publishing:

```sh
python3 scripts/machine-assets.py versions
```

Publication requires committed source. Generated lock/overlay changes are allowed,
but dirty recipes or release metadata are rejected before registry writes so image
provenance points to the exact checked-out Git commit.

## GitHub Actions publication

Push these changes to `main`, or run **Publish versioned OS components** from
Actions. The job uses a GitHub-hosted ARM64 runner and the repository's
`GITHUB_TOKEN` with `packages: write`. It does not use the Oracle build slot.
Relevant recipe, machine-version, Bake or publisher changes trigger the workflow;
rootfs profile, agent-only and documentation changes do not republish components.

The workflow checks input tags first. Existing hardware is migrated by reusing its
exact old artifact layers and repackaging only OCI labels. This does not run a
kernel, firmware or bootloader compiler. Only a missing source/input fingerprint
causes compilation. The first migration builds the small Rootfs Base image and
repackages the four already-published hardware images. Later unchanged runs do not
build, repackage or retag anything. Promotion between stable/dev families also
reuses exact matching source inputs; changing channels does not compile hardware.

The summary lists reused, repackaged and built components plus exact published
references. Authentication/network failures normally fail the check before an
expensive compile. If GHCR returns 403 because a **new namespace has not been
created**, rerun the workflow manually with `create_namespaces` enabled. This is
an explicit first-publication bootstrap, not a fix for invalid credentials.
Rootfs Base publishes first, so a write-permission failure occurs before any
hardware compile. Use this bootstrap only for expected new packages.

New GHCR packages may initially be private. Set each newly created package family
public in GitHub package settings for anonymous discovery/pulls. Alternatively,
configure read-only pull credentials separately on the platform and builder.
Credentials belong on hosts, never in any component or device image.

Stable runs save all five digest pins in `machine-assets.lock.json` (schema 2)
and `hosted-machine-assets.hcl`, then open a review PR. If GitHub policy blocks
Actions from opening PRs, the workflow keeps the pushed pin branch and supplies a
manual compare link. Review/merge the pins, build and test the Pi image before
releasing the reviewed OS recipe. A `dev` run uploads the pins as an Actions
artifact but does not automatically propose them as the stable foundation pins.

## Compatibility and migration

A shared immutable 64-character tag identifies a reviewed hardware combination.
Rootfs Base is versioned independently; updating Alpine or the agent does not
force kernel/bootloader/firmware compilation. Existing source-input hashes are
preserved, allowing reuse of already-published components. Legacy flat package
paths and pins remain valid; additive aliases support older consumers without
replacing an existing immutable legacy reference. Do not delete versions still
referenced by a lock or OS version.

The schema-2 consumption overlay pins Rootfs Base, kernel, bootloader, firmware and
initramfs. The build graph consumes only rootfs/image targets and the necessary
builder tooling, not component source targets. The resolver discovers canonical
versioned packages even before a generated pin PR is merged. Older source revisions
without version metadata continue using legacy discovery. An image that was
explicitly selected/pinned cannot silently fall back to a different component.
Missing unpinned components retain the existing source-build fallback behavior.

```sh
python3 scripts/resolve-machine-assets.py
docker buildx bake -f docker-bake.hcl -f machines/raspberry-pi-3.hcl \
  -f .nulix/machine-assets.hcl ostree-rootfs ostree-sd-image
```

Or, after merging the generated pins:

```sh
python3 scripts/machine-assets.py check
docker buildx bake -f docker-bake.hcl -f machines/raspberry-pi-3.hcl \
  -f hosted-machine-assets.hcl
```

Manual publication on an authenticated ARM64 Docker host:

```sh
python3 scripts/machine-assets.py publish --repository nulix/nulix-alpine
# Development family namespaces:
python3 scripts/machine-assets.py publish --repository nulix/nulix-alpine --channel dev
```

## Device update boundary

The flashable image includes bootloader and boot firmware. OSTree deployment does
not automatically replace U-Boot or all boot-partition firmware. Bootloader changes
remain disabled for subsequent user OS versions, and firmware changes still require
a supported boot-file delivery path or a newly flashed image. Keep the recipe's
`delivery: reflash` policy until that separate OTA path is implemented and tested.
