"""M8: the Pi 5 boot-file edits and the deploy files (the Pi itself is a manual step)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("pi5_boot_config", REPO / "scripts" / "pi5_boot_config.py")
boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(boot)

STOCK_CONFIG = """# For more options and information see
# http://rptl.io/configtxt
dtparam=audio=on
camera_auto_detect=1
display_auto_detect=1
auto_initramfs=1
dtoverlay=vc4-kms-v3d
max_framebuffers=2
disable_fw_kms_setup=1
arm_64bit=1
disable_overscan=1
arm_boost=1

[cm4]
otg_mode=1

[all]
"""
STOCK_CMDLINE = ("console=serial0,115200 console=tty1 root=PARTUUID=4e639091-02 rootfstype=ext4 fsck.repair=yes "
                 "rootwait quiet splash plymouth.ignore-serial-consoles cfg80211.ieee80211_regdom=IN\n")


def test_config_txt_gets_uart0_once_under_all():
    once = boot.edit_config_txt(STOCK_CONFIG)
    assert once.count("dtoverlay=uart0-pi5") == 1
    # it must not land under [cm4]: the last section header before it is [all]
    before = once[:once.index("dtoverlay=uart0-pi5")]
    assert before.rstrip().rsplit("[", 1)[1].startswith("all]")
    assert boot.edit_config_txt(once) == once                       # idempotent
    assert "rtc_bbat_vchg" not in once


def test_enable_uart_is_commented_out_and_rtc_charge_is_opt_in():
    text = boot.edit_config_txt(STOCK_CONFIG + "enable_uart=1\n", rtc_charge=True)
    assert "\nenable_uart=1" not in text and "# enable_uart=1" in text
    assert "dtparam=rtc_bbat_vchg=3000000" in text


def test_cmdline_loses_only_the_serial_console():
    new = boot.edit_cmdline_txt(STOCK_CMDLINE)
    assert "console=serial0" not in new and "console=tty1" in new
    assert new.count("\n") == 1 and new.startswith("console=tty1 root=PARTUUID")
    assert boot.edit_cmdline_txt(new) == new
    assert "console=ttyAMA0" not in boot.edit_cmdline_txt("console=ttyAMA0,115200 root=/dev/sda2\n")


def test_cli_dry_run_changes_nothing(tmp_path, capsys):
    config, cmdline = tmp_path / "config.txt", tmp_path / "cmdline.txt"
    config.write_text(STOCK_CONFIG, encoding="utf-8")
    cmdline.write_text(STOCK_CMDLINE, encoding="utf-8")
    assert boot.main(["--dry-run", "--config", str(config), "--cmdline", str(cmdline)]) == 0
    assert config.read_text(encoding="utf-8") == STOCK_CONFIG
    assert "+dtoverlay=uart0-pi5" in capsys.readouterr().out
    assert boot.main(["--config", str(config), "--cmdline", str(cmdline)]) == 0
    assert "dtoverlay=uart0-pi5" in config.read_text(encoding="utf-8")
    assert list(tmp_path.glob("config.txt.storemind-*.bak"))                   # backup kept


def test_udev_rule_names_the_header_uart_not_serial0():
    rule = (REPO / "deploy" / "pi5" / "99-storemind-mcu.rules").read_text(encoding="utf-8")
    active = [ln for ln in rule.splitlines() if ln and not ln.startswith("#")]
    assert active == ['KERNEL=="ttyAMA0", SYMLINK+="storemind-mcu", GROUP="dialout", MODE="0660", TAG+="systemd"']


def test_chrony_serves_the_lan_and_keeps_serving_offline():
    conf = (REPO / "deploy" / "pi5" / "chrony-storemind.conf").read_text(encoding="utf-8")
    assert "allow 192.168.0.0/16" in conf and "local stratum 10" in conf
