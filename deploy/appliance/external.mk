include $(sort $(wildcard $(BR2_EXTERNAL_SHUSH_PATH)/package/*/*.mk))

# Built-in radio drivers can request firmware before the SD root is mounted.
# Linux already depends on linux-firmware and wireless-regdb when selected.
LINUX_DEPENDENCIES += shush-bt-firmware

define SHUSH_LINUX_FIRMWARE_DIRECTORY
	$(call KCONFIG_SET_OPT,CONFIG_EXTRA_FIRMWARE_DIR,"$(TARGET_DIR)/lib/firmware")
endef
LINUX_POST_CONFIGURE_HOOKS += SHUSH_LINUX_FIRMWARE_DIRECTORY

define SHUSH_LINUX_AUTHORIZATION_PATCH
	$(APPLY_PATCHES) $(@D) $(BR2_EXTERNAL_SHUSH_PATH)/patches/linux '*.patch'
endef
LINUX_POST_PATCH_HOOKS += SHUSH_LINUX_AUTHORIZATION_PATCH
