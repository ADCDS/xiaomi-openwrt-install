"""The catalogues: selection, fallback, and that none has gone stale.

Every check that used to look only at Russian now runs over every catalogue in
`i18n.CATALOGUES`, so adding a language cannot silently ship a half-translated
one: the new catalogue has to carry the same keys, the same placeholders, the
same leading newline and trailing space, and the same Latin confirmation words.
"""
import ast
import os
import string
import unittest
from unittest import mock

import i18n

# Files whose t("...") calls the catalogues serve.
SOURCES = ("install.py", "revert.py", "updater.py")
HERE = os.path.dirname(os.path.abspath(__file__))

# The confirmations an operator must type. Kept in Latin letters in every
# language: an IME or a non-Latin keyboard layout must not change the token.
TYPED = {
    "[?] {question} [type YES to continue] ": "YES",
    "[?] write {image} to ubi_kernel and reboot? [type YES] ": "YES",
    "[?] erase OpenWrt and write stock? [type RESTORE] ": "RESTORE",
}


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
    def setUp(self):
        self.templates = templates_in_source()

    def test_every_language_has_a_catalogue(self):
        self.assertEqual(set(i18n.TRANSLATED), set(i18n.CATALOGUES))
        self.assertEqual(set(i18n.LANGUAGES) - {"auto", "en"}, set(i18n.CATALOGUES))

    def test_every_language_is_selectable(self):
        for language in i18n.LANGUAGES:
            with self.subTest(language=language):
                i18n.set_language(language)
        self.addCleanup(i18n.set_language, "en")

    def test_every_source_template_is_translated(self):
        for language, catalogue in i18n.CATALOGUES.items():
            with self.subTest(language=language):
                self.assertEqual(set(self.templates) - set(catalogue), set())

    def test_every_translation_is_still_used(self):
        for language, catalogue in i18n.CATALOGUES.items():
            with self.subTest(language=language):
                self.assertEqual(set(catalogue) - set(self.templates), set())

    def test_placeholders_match_the_english_template(self):
        for language, catalogue in i18n.CATALOGUES.items():
            for english, translated in catalogue.items():
                with self.subTest(language=language, tex=english):
                    self.assertEqual(fields(english), fields(translated), english)

    def test_call_sites_pass_every_placeholder(self):
        for template, passed in self.templates.items():
            self.assertEqual(fields(template), passed, template)

    def test_what_must_be_typed_stays_latin(self):
        for language, catalogue in i18n.CATALOGUES.items():
            for template, word in TYPED.items():
                with self.subTest(language=language, word=word):
                    self.assertIn(word, catalogue[template])

    def test_prompts_keep_their_trailing_space(self):
        for language, catalogue in i18n.CATALOGUES.items():
            for english, translated in catalogue.items():
                with self.subTest(language=language, tex=english):
                    self.assertEqual(english.endswith(" "), translated.endswith(" "), english)
                    self.assertEqual(english.startswith("\n"), translated.startswith("\n"),
                                     english)

    def test_nothing_is_left_blank(self):
        for language, catalogue in i18n.CATALOGUES.items():
            blank = [key for key, text in catalogue.items() if not text.strip()]
            with self.subTest(language=language):
                self.assertEqual(blank, [])

    def test_a_catalogue_is_not_a_copy_of_the_english_one(self):
        # A few entries are legitimately identical (a bare "SSID:" prompt, a
        # command name), so this only refuses a catalogue that is mostly English.
        for language, catalogue in i18n.CATALOGUES.items():
            same = sum(1 for key, text in catalogue.items() if text == key)
            with self.subTest(language=language):
                self.assertLess(same, len(catalogue) // 2, f"{language} looks untranslated")


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

    def test_every_language_translates_and_formats(self):
        template = "[3] waiting up to {seconds}s for permanent OpenWrt on {where}"
        for language, catalogue in i18n.CATALOGUES.items():
            with self.subTest(language=language):
                i18n.set_language(language)
                self.assertEqual(i18n.t(template, seconds=600, where="eth0"),
                                 catalogue[template].format(seconds=600, where="eth0"))

    def test_values_are_not_reinterpreted(self):
        for language in i18n.CATALOGUES:
            with self.subTest(language=language):
                i18n.set_language(language)
                self.assertIn("{odd}", i18n.t(
                    "[*] join permanent SSID {ssid!r} on {iface}; verification "
                    "will wait without printing its password", ssid="{odd}", iface="wlan0"))

    def test_unknown_text_falls_back_to_english(self):
        for language in i18n.CATALOGUES:
            with self.subTest(language=language):
                i18n.set_language(language)
                self.assertEqual(i18n.t("not in the catalogue"), "not in the catalogue")

    def test_default_is_english(self):
        i18n.set_language("en")
        self.assertEqual(i18n.t("declined"), "declined")

    def test_auto_from_locale(self):
        self.assertEqual(self.detect(LANG="ru_RU.UTF-8"), "отменено")
        self.assertEqual(self.detect(LANG="zh_CN.UTF-8"), "已取消")
        self.assertEqual(self.detect(LANG="pt_BR.UTF-8"), "recusado")
        self.assertEqual(self.detect(LANG="en_US.UTF-8"), "declined")
        self.assertEqual(self.detect(), "declined")
        self.assertEqual(self.detect(LC_ALL="C", LANG="ru_RU.UTF-8"), "declined")

    def test_environment_override_wins(self):
        self.assertEqual(self.detect(LANG="ru_RU.UTF-8", XIAOMI_INSTALL_LANG="en"), "declined")
        self.assertEqual(self.detect(LANG="en_US", XIAOMI_INSTALL_LANG="ru"), "отменено")
        self.assertEqual(self.detect(LANG="en_US", XIAOMI_INSTALL_LANG="zh_CN"), "已取消")
        self.assertEqual(self.detect(LANG="en_US", XIAOMI_INSTALL_LANG="pt-br"), "recusado")

    def test_region_and_alias_spellings_pick_the_same_catalogue(self):
        # What a locale, or XIAOMI_INSTALL_LANG, may say. argparse's --lang
        # choices are the canonical codes only, so these go through
        # set_language() rather than a parser.
        for spelling, expected in (("zh-CN", "zh-CN"), ("zh_CN", "zh-CN"), ("zh-Hans", "zh-CN"),
                                   ("cn", "zh-CN"), ("pt-BR", "pt-BR"), ("pt_br", "pt-BR"),
                                   ("pt", "pt-BR"), ("ru", "ru"), ("en", "en")):
            with self.subTest(spelling=spelling):
                i18n.set_language(spelling)
                self.assertEqual(i18n.t("declined"), i18n.CATALOGUES.get(expected, {}).get(
                    "declined", "declined"))

    def test_rejects_unknown_language(self):
        for bad in ("de", "xx-YY", ""):
            with self.subTest(language=bad):
                with self.assertRaises(ValueError):
                    i18n.set_language(bad)


if __name__ == "__main__":
    unittest.main()
