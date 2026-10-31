import importlib.util
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup

from vp_helper.submission_confirmation import confirm_submission
from vp_helper.models import VPError


class ConfirmationTests(unittest.TestCase):
    def test_explicit_yes_keeps_preview_and_permission_check_without_reading_input(self):
        config={'name':'Contest'}
        with patch('vp_helper.cloud.check_submission') as access,patch('builtins.input') as decision,patch('sys.stdout',io.StringIO()) as output:
            self.assertTrue(confirm_submission(Path('A.cpp'),config,'A','C++20','https://example.invalid/submit',assume_yes=True))
        access.assert_called_once_with(config);decision.assert_not_called()
        self.assertIn('Submission information:',output.getvalue());self.assertIn('y [-y]',output.getvalue())
        with patch('vp_helper.cloud.check_submission',side_effect=VPError('denied')),patch('builtins.input') as decision:
            with self.assertRaisesRegex(VPError,'denied'):
                confirm_submission(Path('A.cpp'),config,'A','C++20','https://example.invalid/submit',assume_yes=True)
        decision.assert_not_called()

    def test_blank_eof_and_interrupt_cancel_and_invalid_input_does_not_confirm(self):
        for answers in ([''],[EOFError()],[KeyboardInterrupt()],['invalid','n']):
            with patch('builtins.input',side_effect=answers),patch('sys.stdout',io.StringIO()):
                self.assertFalse(confirm_submission(Path('A.cpp'),{'name':'Contest'},'A','C++20','https://example.invalid/submit'))

    def test_bundled_cf_and_gym_vp_use_real_confirmation_before_submit_form(self):
        path=Path(__file__).resolve().parents[1]/'third_party/cf_submit/cf_submit.py'
        spec=importlib.util.spec_from_file_location('fixture_cf_submit',path)
        module=importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules',{'colours':Mock()}):
            spec.loader.exec_module(module)
        class Form(dict):
            def __setitem__(self,key,value):
                if key in self:
                    self[key].value=value
                else:
                    super().__setitem__(key,SimpleNamespace(value=value))
        for kind,ident in (('contest','123'),('gym','100123')):
            for answer in ('n','y'):
                with self.subTest(kind=kind,answer=answer),tempfile.TemporaryDirectory() as tmp:
                    source=Path(tmp)/'A.cpp'
                    source.write_text('int main(){}')
                    form=Form(programTypeId=SimpleNamespace(options=['73'],labels=['GNU G++20']),
                              submittedProblemIndex=SimpleNamespace(value='A'))
                    browser=Mock(url=f'https://codeforces.com/{kind}/{ident}/submit/A')
                    browser.get_form.return_value=form
                    browser.parsed=BeautifulSoup('','html.parser')
                    browser.session.get.return_value=SimpleNamespace(status_code=200,
                        url=f'https://codeforces.com/{kind}/{ident}/my',text='')
                    browser.submit_form.side_effect=lambda f, **kwargs: setattr(browser,'url',f'https://codeforces.com/{kind}/{ident}/my')
                    with patch('builtins.input',return_value=answer),patch('sys.stdout',io.StringIO()) as out:
                        module.submit(browser,'tester',ident,'A','cpp',str(source),False,False)
                    self.assertEqual(browser.submit_form.call_count,int(answer=='y'))
                    self.assertIn('GNU G++20',out.getvalue())
                    self.assertIn(f'https://codeforces.com/{kind}/{ident}/submit/A',out.getvalue())
                    if answer == 'y':
                        self.assertIn(f'Check judging results here: https://codeforces.com/{kind}/{ident}/my',out.getvalue())
                    else:
                        self.assertNotIn('Check judging results here:',out.getvalue())
