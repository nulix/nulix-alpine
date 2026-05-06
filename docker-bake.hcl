variable "MACHINE_NAME" {
  default = "raspberry-pi-3"
}

variable "MACHINE_ARCH" {
  default = "arm64"
}

variable "MACHINE_VIRT" {
  default = "docker"
}

group "default" {
  targets = ["nulix-builder", "firmware", "ostree-initramfs", "linux-kernel", "u-boot", "ostree-rootfs", "ostree-sd-image"]
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

  args = {
    MACHINE_NAME = "${MACHINE_NAME}"
    FIRMWARE_REPO = "https://github.com/raspberrypi/firmware.git"
    FIRMWARE_REV = "f1ea7092589bc9627c23916132baa7841932b707"
  }
  
  output = ["type=local,dest=./out/firmware"]
}

target "ostree-initramfs" {
  context = "./recipes/ostree-initramfs"
  platforms = ["linux/arm64"]

  args = {
    MACHINE_ARCH = "${MACHINE_ARCH}"
  }

  output = ["type=local,dest=./out/ostree-initramfs"]
}

target "linux-kernel" {
  context = "./recipes/linux-kernel"
  platforms = ["linux/arm64"]

  contexts = {
    builder = "target:nulix-builder"
  }

  args = {
    MACHINE_ARCH = "${MACHINE_ARCH}"
    KERNEL_REPO = "https://github.com/raspberrypi/linux.git"
    KERNEL_REV = "a1073743767f9e7fdc7017ababd2a07ea0c97c1c"
    KERNEL_DEFCONFIG = "bcm2711_defconfig"
    KERNEL_IMAGE = "Image"
  }

  output = ["type=local,dest=./out/linux-kernel"]
}

target "u-boot" {
  context = "./recipes/u-boot"
  platforms = ["linux/arm64"]

  contexts = {
    builder = "target:nulix-builder"
  }

  args = {
    MACHINE_NAME = "${MACHINE_NAME}"
    UBOOT_REPO = "https://github.com/u-boot/u-boot.git"
    UBOOT_REV = "e50b1e8715011def8aff1588081a2649a2c6cd47"
    UBOOT_DEFCONFIG = "rpi_3_b_plus_defconfig"
  }

  output = ["type=local,dest=./out/u-boot"]
}

target "ostree-rootfs" {
  context = "./recipes/ostree-rootfs"
  platforms = ["linux/arm64"]

  args = {
    MACHINE_NAME = "${MACHINE_NAME}"
    MACHINE_ARCH = "${MACHINE_ARCH}"
    MACHINE_VIRT = "${MACHINE_VIRT}"
    PACKAGES = "alpine-base,busybox,openssh,ca-certificates,tzdata,curl,chrony,dosfstools,e2fsprogs,e2fsprogs-extra,parted,htop,iproute2,agetty,agetty-openrc,openrc"
  }

  contexts = {
    linux-kernel = "target:linux-kernel"
    ostree-initramfs = "target:ostree-initramfs"
    builder = "target:nulix-builder"
  }

  output = ["type=local,dest=./out/ostree-rootfs"]
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

  output = ["type=local,dest=./out/ostree-sd-image"]
}
