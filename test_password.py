"""Oracle password verifier vectors from the kvm-lab project."""
import contextlib
import io
import unittest
from unittest import mock

from rico2 import Rico, execute_command, generate_oracle_password_verifiers


ORACLE_HR_VERIFIERS = (
    "S:9DEA3FA1EDC6087AD05C7E8AE3FD179D4E93CF9BBE12100B315AB84BC247;"
    "T:7900765EBCE54DE4840EDF890A8D927BBE6608BD9E811C653FB7D1B779E647F"
    "2346923D96E4E75B5F0029CF930BA4AA3C388EF27294368EE0C20900CA96768471"
    "AE60F59B8252311BBE41EE4C5DABDBF"
)
S_SALT = bytes.fromhex("be12100b315ab84bc247")
T_SALT = bytes.fromhex("1ae60f59b8252311bbe41ee4c5dabdbf")


class PasswordVerifierTests(unittest.TestCase):
    def test_oracle_hr_reference_vector_for_both_versions(self):
        self.assertEqual(
            ORACLE_HR_VERIFIERS,
            generate_oracle_password_verifiers("hr", S_SALT, T_SALT))
        self.assertNotEqual(
            ORACLE_HR_VERIFIERS,
            generate_oracle_password_verifiers("HR", S_SALT, T_SALT))

    def test_command_outputs_spare4_form_without_selected_block(self):
        rico = Rico()
        output = io.StringIO()
        with mock.patch("rico2.os.urandom", side_effect=(S_SALT, T_SALT)) as random_bytes:
            with contextlib.redirect_stdout(output):
                self.assertTrue(execute_command(rico, 'GeNpWd "hr"'))
        self.assertEqual(ORACLE_HR_VERIFIERS + "\n", output.getvalue())
        self.assertEqual([mock.call(10), mock.call(16)], random_bytes.call_args_list)
        self.assertIsNone(rico.block_data)
        self.assertFalse(rico.dirty)

    def test_fresh_salts_utf8_and_quoted_spaces(self):
        rico = Rico()
        outputs = []
        for _ in range(2):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                execute_command(rico, 'genpwd "Zażółć gęślą"')
            verifier = output.getvalue().strip()
            self.assertRegex(verifier, r"^S:[0-9A-F]{60};T:[0-9A-F]{160}$")
            outputs.append(verifier)
        self.assertNotEqual(outputs[0], outputs[1])

    def test_invalid_password_and_salts_are_rejected_without_output(self):
        for password in ("", "a\x00b", "a\nb", "a\rb"):
            with self.subTest(password=password), self.assertRaises(ValueError):
                generate_oracle_password_verifiers(password, S_SALT, T_SALT)
        for s, t in ((b"short", T_SALT), (S_SALT, b"short"), ("not bytes", T_SALT)):
            with self.subTest(s=s, t=t), self.assertRaises(ValueError):
                generate_oracle_password_verifiers("hr", s, t)
        for command in ("genpwd", "genpwd hr extra", "genpwd ''", "genpwd 'a\nb'"):
            with self.subTest(command=command):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(ValueError):
                    execute_command(Rico(), command)
                self.assertEqual("", output.getvalue())


if __name__ == "__main__":
    unittest.main()
