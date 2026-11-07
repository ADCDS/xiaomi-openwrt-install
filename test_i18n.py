"""The Russian catalogue: selection, fallback, and that it has not gone stale."""
import ast
import os
import string
import unittest
from unittest import mock

import i18n

# Files whose t("...") calls the catalogue serves.
SOURCES = ("install.py", "revert.py", "updater.py")
HERE = os.path.dirname(os.path.abspath(__file__))


def templates_in_source():
    """Every string literal handed to t(), with implicit concatenation resolved."""
    found = {}
    for name in SOURCES:
        with open(os.path.join(HERE, name), encoding="utf-8") as source:
            tree = ast.parse(source.read())
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "t"):
                first = node.args[0]
                # A conditional picks between two literal messages.
                choices = ([first.body, first.orelse] if isinstance(first, ast.IfExp)
                           else [first])
                for choice in choices:
                    if not (isinstance(choice, ast.Constant) and isinstance(choice.value, str)):
                        raise AssertionError(f"{name}:{node.lineno}: t() needs a literal")
                    found[choice.value] = {kw.arg for kw in node.keywords}
    return found


def fields(template):
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


class CatalogueTests(unittest.TestCase):
    def test_every_source_template_is_translated(self):
        missing = set(templates_in_source()) - set(i18n.RU)
        self.assertEqual(missing, set())

    def test_every_translation_is_still_used(self):
        stale = set(i18n.RU) - set(templates_in_source())
        self.assertEqual(stale, set())

    def test_placeholders_match_on_both_sides(self):
        for english, russian in i18n.RU.items():
            self.assertEqual(fields(english), fields(russian), english)

    def test_call_sites_pass_every_placeholder(self):
        for template, passed in templates_in_source().items():
            self.assertEqual(fields(template), passed, template)

    def test_what_must_be_typed_stays_latin(self):
        self.assertIn("YES", i18n.RU["[?] {question} [type YES to continue] "])
        self.assertIn("YES", i18n.RU["[?] write {image} to ubi_kernel and reboot? [type YES] "])
        self.assertIn("RESTORE", i18n.RU["[?] erase OpenWrt and write stock? [type RESTORE] "])

    def test_prompts_keep_their_trailing_space(self):
        for english, russian in i18n.RU.items():
            self.assertEqual(english.endswith(" "), russian.endswith(" "), english)
            self.assertEqual(english.startswith("\n"), russian.startswith("\n"), english)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(i18n.set_language, "en")

    def detect(self, platform="linux", **env):
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(i18n.sys, "platform", platform):
            i18n.set_language("auto")
        return i18n.t("declined")

    def test_translates_and_formats(self):
        i18n.set_language("ru")
        self.assertEqual(i18n.t("[3] waiting up to {seconds}s for permanent OpenWrt on {where}",
                                seconds=600, where="eth0"),
                         "[3] ожидание постоянного OpenWrt на eth0, до 600 с")
        self.assertEqual(i18n.t("[2] Wi-Fi-only: when the stock network disappears, "
                                "join {ssid!r} with key {key!r}", ssid="a", key="b"),
                         "[2] Установка по Wi-Fi: когда заводская сеть пропадёт, "
                         "подключитесь к 'a', пароль 'b'")

    def test_values_are_not_reinterpreted(self):
        i18n.set_language("ru")
        self.assertIn("{odd}", i18n.t("[*] join permanent SSID {ssid!r} on {iface}; verification "
                                      "will wait without printing its password",
                                      ssid="{odd}", iface="wlan0"))

    def test_unknown_text_falls_back_to_english(self):
        i18n.set_language("ru")
        self.assertEqual(i18n.t("not in the catalogue"), "not in the catalogue")

    def test_default_is_english(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("declined"), "declined")

    def test_auto_from_locale(self):
        self.assertEqual(self.detect(LANG="ru_RU.UTF-8"), "отменено")
        self.assertEqual(self.detect(LANG="en_US.UTF-8"), "declined")
        self.assertEqual(self.detect(), "declined")
        self.assertEqual(self.detect(LC_ALL="C", LANG="ru_RU.UTF-8"), "declined")

    def test_environment_override_wins(self):
        self.assertEqual(self.detect(LANG="ru_RU.UTF-8", XIAOMI_INSTALL_LANG="en"), "declined")
        self.assertEqual(self.detect(LANG="en_US", XIAOMI_INSTALL_LANG="ru"), "отменено")

    def test_rejects_unknown_language(self):
        with self.assertRaises(ValueError):
            i18n.set_language("de")


if __name__ == "__main__":
    unittest.main()
