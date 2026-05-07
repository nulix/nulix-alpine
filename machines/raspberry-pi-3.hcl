variable "MACHINE_NAME" {
  default = "raspberry-pi-3"
}

variable "MACHINE_ARCH" {
  default = "arm64"
}

variable "KERNEL_IMAGE" {
  default = "Image"
}

variable "UBOOT_ENV_SIZE" {
  default = "0x4000"
}

target "firmware" {
  args = {
    MACHINE_NAME = "${MACHINE_NAME}"
    FIRMWARE_REPO = "https://github.com/raspberrypi/firmware.git"
    FIRMWARE_REV = "f1ea7092589bc9627c23916132baa7841932b707"
  }
}

target "u-boot" {
  args = {
    MACHINE_NAME = "${MACHINE_NAME}"
    UBOOT_REPO = "https://github.com/u-boot/u-boot.git"
    UBOOT_REV = "e50b1e8715011def8aff1588081a2649a2c6cd47"
    UBOOT_DEFCONFIG = "rpi_3_b_plus_defconfig"
    UBOOT_ENV_SIZE = "${UBOOT_ENV_SIZE}"
  }
}

target "linux-kernel" {
  args = {
    MACHINE_ARCH = "${MACHINE_ARCH}"
    KERNEL_REPO = "https://github.com/raspberrypi/linux.git"
    KERNEL_REV = "a1073743767f9e7fdc7017ababd2a07ea0c97c1c"
    KERNEL_DEFCONFIG = "bcm2711_defconfig"
    KERNEL_IMAGE = "${KERNEL_IMAGE}"
  }
}

target "ostree-rootfs" {
  args = {
    KERNEL_IMAGE = "${KERNEL_IMAGE}"
    SERIAL_PORT = "ttyS0"
    SERIAL_BAUD = "115200"
    BOOT_DEV_PART = "mmcblk0p1"
    UBOOT_ENV_SIZE = "${UBOOT_ENV_SIZE}"
  }
}
