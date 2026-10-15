SHUSH_BT_FIRMWARE_VERSION = cdf61dc691a49ff01a124752bd04194907f0f9cd
SHUSH_BT_FIRMWARE_SITE = https://github.com/RPi-Distro/bluez-firmware/archive
SHUSH_BT_FIRMWARE_SOURCE = $(SHUSH_BT_FIRMWARE_VERSION).tar.gz
SHUSH_BT_FIRMWARE_LICENSE = Cypress firmware license
SHUSH_BT_FIRMWARE_LICENSE_FILES = debian/firmware/broadcom/LICENSE.cypress

define SHUSH_BT_FIRMWARE_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0644 $(@D)/debian/firmware/broadcom/BCM4345C0.hcd \
		$(TARGET_DIR)/lib/firmware/brcm/BCM4345C0.hcd
	ln -sf BCM4345C0.hcd \
		$(TARGET_DIR)/lib/firmware/brcm/BCM4345C0.raspberrypi,3-model-b-plus.hcd
endef

$(eval $(generic-package))
