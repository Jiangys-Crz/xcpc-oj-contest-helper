import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vp_helper import cloud, hydro_submit, nowcoder_submit, printfile
from vp_helper.models import VPError


class ExplicitConfirmationOptionTests(unittest.TestCase):
    def test_cwnuoj_and_nowcoder_parsers_forward_the_explicit_option_only(self):
        for module in (hydro_submit,nowcoder_submit):
            for flag in (None,'-y','--yes'):
                with self.subTest(module=module.__name__,flag=flag):
                    argv=['submit','A.cpp']+([flag] if flag else [])
                    with patch('sys.argv',argv),patch.object(module,'submit') as submit:
                        module.main()
                    submit.assert_called_once_with(Path('A.cpp'),None,None,**({'assume_yes':True} if flag else {}))

    def test_print_yes_keeps_authorization_and_uploads_exactly_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'A.cpp';source.write_text('int main(){}\n')
            state=root/'state.json';context={'serverUrl':'https://vp.cwnupaa.com','roomId':'room','participantId':'team','startsAt':'2026-10-31T00:00:00Z','roomName':'Room','label':'Team'}
            state.write_text(json.dumps({'cloud':context}));config={'vp_context':{'state_path':str(state)}}
            for flag in ('-y','--yes'):
                with patch('sys.argv',['printfile',str(source),flag]),patch.object(printfile,'submission_configuration',return_value=(root/'contest.json',config)),patch.object(cloud,'ensure_access') as access,patch.object(cloud,'_request',return_value={'id':'job'}) as request,patch('builtins.input') as decision,patch('sys.stdout',io.StringIO()) as output:
                    printfile.main()
                access.assert_called_once();decision.assert_not_called();request.assert_called_once()
                self.assertEqual(request.call_args.args[2]['code'],source.read_text())
                self.assertIn('Virtual print information:',output.getvalue());self.assertIn('y [-y]',output.getvalue())
            with patch('sys.argv',['printfile',str(source),'-y']),patch.object(printfile,'submission_configuration',return_value=(root/'contest.json',config)),patch.object(cloud,'ensure_access',side_effect=VPError('denied')),patch.object(cloud,'_request') as request,patch('sys.stderr',io.StringIO()):
                with self.assertRaises(SystemExit):printfile.main()
            request.assert_not_called()
