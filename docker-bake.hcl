variable "ALPINE_VERSION" {
  default = "3.22.2"
}

variable "MACHINE_VIRT" {
  default = "docker"
}

group "default" {
  targets = ["nulix-builder", "firmware", "ostree-initramfs", "linux-kernel", "u-boot", "ostree-rootfs", "ostree-sd-image"]
}

target "rootfs-base" {
  context = "./recipes/rootfs-base"
  platforms = ["linux/arm64"]
  args = { ALPINE_VERSION = "${ALPINE_VERSION}" }
}

target "nulix-builder" {
  context = "./builder"
}

target "firmware" {
  context = "./recipes/firmware"
  platforms = ["linux/arm64"]

  contexts = {
    builder = "target:nulix-builder"
  }

  output = ["type=local,dest=./out/${MACHINE_NAME}/firmware"]
}

target "u-boot" {
  context = "./recipes/u-boot"
  platforms = ["linux/arm64"]

  contexts = {
    builder = "target:nulix-builder"
  }

  output = ["type=local,dest=./out/${MACHINE_NAME}/u-boot"]
}

target "linux-kernel" {
  context = "./recipes/linux-kernel"
  platforms = ["linux/arm64"]

  contexts = {
    builder = "target:nulix-builder"
  }

  output = ["type=local,dest=./out/${MACHINE_NAME}/linux-kernel"]
}

target "ostree-initramfs" {
  context = "./recipes/ostree-initramfs"
  platforms = ["linux/arm64"]

  args = {
    MACHINE_ARCH = "${MACHINE_ARCH}"
  }

  output = ["type=local,dest=./out/${MACHINE_NAME}/ostree-initramfs"]
}

target "ostree-rootfs" {
  context = "./recipes/ostree-rootfs"
  platforms = ["linux/arm64"]

  args = {
    MACHINE_NAME = "${MACHINE_NAME}"
    MACHINE_ARCH = "${MACHINE_ARCH}"
    MACHINE_VIRT = "${MACHINE_VIRT}"
    PACKAGES = "alpine-base,busybox,openssh,ca-certificates,tzdata,curl,chrony,dosfstools,e2fsprogs,e2fsprogs-extra,parted,htop,iproute2,agetty,agetty-openrc,openrc,openrc-init,sudo"
  }

  contexts = {
    rootfs-base = "target:rootfs-base"
    linux-kernel = "target:linux-kernel"
    ostree-initramfs = "target:ostree-initramfs"
    builder = "target:nulix-builder"
  }

  output = ["type=local,dest=./out/${MACHINE_NAME}/ostree-rootfs"]
}

target "ostree-sd-image" {
  context = "./recipes/ostree-sd-image"
  platforms = ["linux/arm64"]

  contexts = {
    linux-kernel = "target:linux-kernel"
    u-boot = "target:u-boot"
    firmware = "target:firmware"
    ostree-rootfs = "target:ostree-rootfs"
    builder = "target:nulix-builder"
  }

  args = {
    OTA_SYSROOT = "/sysroot"
    OSTREE_OSNAME = "nulix"
    OSTREE_BRANCHNAME = "stable"
    IMAGE_FILE = "nulix-os.img"
  }

  output = ["type=local,dest=./out/${MACHINE_NAME}/ostree-sd-image"]
}
