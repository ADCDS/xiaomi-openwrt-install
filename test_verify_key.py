"""Read-only verification with OpenSSH keys; no real network access."""
import subprocess
import unittest
from unittest.mock import patch
import install


class VerificationKeyTests(unittest.TestCase):
    def setUp(self):
        language = patch.dict(install.os.environ, {"XIAOMI_INSTALL_LANG": "en"})
        language.start()
        self.addCleanup(language.stop)
        install.i18n.set_language("en")

    def test_key_command_is_noninteractive_and_checks_host_identity(self):
        with patch.object(install, 'VERIFY_SSH_KEY', '/tmp/my key'), \
             patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'board', '')) as run:
            self.assertEqual(install.ssh('192.168.1.1', 'board_name'), (0, 'board'))
            argv = run.call_args.args[0]
            self.assertIn('StrictHostKeyChecking=yes', argv)
            self.assertIn('BatchMode=yes', argv)
            self.assertIn('PreferredAuthentications=publickey', argv)
            self.assertEqual(argv[argv.index('-i') + 1], '/tmp/my key')
            self.assertNotIn('sshpass', argv)

    def test_failure_does_not_fall_back_to_password(self):
        with patch.object(install, 'VERIFY_SSH_KEY', 'agent'), \
             patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 255, '', 'Permission denied')) as run:
            self.assertEqual(install.ssh('192.168.1.1', 'board_name', check=False), (255, 'Permission denied'))
            run.assert_called_once()
            self.assertNotIn('-i', run.call_args.args[0])

    def test_key_flags_cannot_enable_writes(self):
        with self.assertRaisesRegex(install.Abort, 'require --verify-only'):
            install.main(['--verify-ssh-key', 'agent'])
        self.assertIsNone(install.VERIFY_SSH_KEY)

    def test_authentication_error_is_not_reported_as_wrong_board(self):
        with patch.object(install, 'VERIFY_SSH_KEY', 'agent'), \
             patch.object(install, 'check_is_openwrt_ram'), \
             patch.object(install, 'ssh', return_value=(255, 'Permission denied')), \
             patch.object(install, 'log'):
            with self.assertRaisesRegex(install.Abort, 'Key verification failed.*Permission denied'):
                install.verify_permanent_system(None, host='192.168.1.1', host_iface='en0')


if __name__ == '__main__':
    unittest.main()
