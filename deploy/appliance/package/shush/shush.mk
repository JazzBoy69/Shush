################################################################################
#
# shush -- local sources, Buildroot 2026.08 cargo infrastructure
#
################################################################################

SHUSH_VERSION = 0.1.0
SHUSH_SITE = $(abspath $(BR2_EXTERNAL_SHUSH_PATH)/../..)
SHUSH_SITE_METHOD = local
SHUSH_DEPENDENCIES = alsa-lib host-pkgconf

# Compile a clean source snapshot. Media is installed separately below, so
# archived voice banks, preparation environments, and host target/ binaries
# cannot become part of either the compilation or the appliance filesystem.
SHUSH_OVERRIDE_SRCDIR_RSYNC_EXCLUSIONS = \
	--include=/Cargo.toml \
	--include=/Cargo.lock \
	--include=/src/*** \
	--exclude=*

# Local packages bypass Buildroot's download-time cargo postprocessing.
# Mirror its locked vendoring step before invoking the cargo infrastructure's
# --offline --locked cross build. The lock also protects the shared Cargo home.
define SHUSH_CONFIGURE_CMDS
	mkdir -p $(@D)/.cargo $(BR_CARGO_HOME)
	cd $(@D) && $(HOST_MAKE_ENV) $(PKG_COMMON_CARGO_ENV) \
		flock $(BR_CARGO_HOME)/.br-lock \
		$(HOST_DIR)/bin/cargo vendor --locked VENDOR > .cargo/config.toml
endef

# Buildroot's pkg-cargo.mk assigns profile defaults through the environment;
# retain this application's release checks and compilation profile explicitly.
# TARGET_CONFIGURE_OPTS supplies the cross pkg-config wrapper and sysroot.
SHUSH_CARGO_ENV = \
	PKG_CONFIG_ALLOW_CROSS=1 \
	CARGO_TARGET_DIR=$(@D)/target \
	CARGO_PROFILE_DEV_OVERFLOW_CHECKS=true \
	CARGO_PROFILE_RELEASE_OVERFLOW_CHECKS=true \
	CARGO_PROFILE_RELEASE_OPT_LEVEL=3 \
	CARGO_PROFILE_RELEASE_LTO=thin \
	CARGO_PROFILE_RELEASE_CODEGEN_UNITS=1
SHUSH_CARGO_BUILD_OPTS = --bin shush
SHUSH_BUILD_PROFILE = $(if $(BR2_ENABLE_DEBUG),debug,release)

define SHUSH_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 \
		$(@D)/target/$(RUSTC_TARGET_NAME)/$(SHUSH_BUILD_PROFILE)/shush \
		$(TARGET_DIR)/opt/shush/bin/shush
	$(INSTALL) -d -m 0755 $(TARGET_DIR)/opt/shush/Audio \
		$(TARGET_DIR)/opt/shush/voice $(TARGET_DIR)/opt/shush/reference
	$(INSTALL) -m 0644 $(SHUSH_SITE)/Audio/*.mp3 $(TARGET_DIR)/opt/shush/Audio/
	$(INSTALL) -m 0644 $(SHUSH_SITE)/voice/*.wav $(TARGET_DIR)/opt/shush/voice/
	$(INSTALL) -m 0644 $(SHUSH_SITE)/Pink_Noise.wav $(TARGET_DIR)/opt/shush/
	$(INSTALL) -m 0644 $(SHUSH_SITE)/reference/reading_schedule.json \
		$(TARGET_DIR)/opt/shush/reference/
endef

$(eval $(cargo-package))
