"""Upload a confirmed virtual print job to the local contest's accepted team."""
from pathlib import Path
import sys
from . import cloud
from .models import VPError
from .storage import read_json
from .submit_context import submission_configuration


def main():
    args = sys.argv[1:]
    if args in ([], ['--help'], ['-h']):
        print('Usage: printfile SOURCE\nPrint source code to your online VP team only.')
        return
    try:
        if len(args) != 1:
            raise VPError('Usage: printfile SOURCE')
        source = Path(args[0]).expanduser().resolve()
        if not source.is_file() or source.stat().st_size > 512*1024:
            raise VPError('Choose a UTF-8 source file no larger than 512 KiB.')
        try:
            code = source.read_text(encoding='utf8')
        except UnicodeError:
            raise VPError('The source file must use UTF-8 encoding.') from None
        _, config = submission_configuration(source)
        path = config.get('vp_context', {}).get('state_path')
        state = read_json(Path(path)) if path else {}
        context = state.get('cloud')
        if not context:
            raise VPError('Virtual printing is available only in an online VP room workspace.')
        cloud.ensure_access(state)
        language = {'.cpp':'C++','.cc':'C++','.c':'C','.py':'Python','.java':'Java','.rs':'Rust'}.get(source.suffix.lower(),'Text')
        print('Virtual print information:')
        print('  filename:    ' + source.name)
        print('  language:    ' + language)
        print('  room:        ' + context['roomName'])
        print('  participant: ' + context['label'])
        print('  size:        ' + str(len(code.encode('utf8'))) + ' bytes')
        while True:
            try:
                answer = input('Do you really want to print this file? (y/n) ').strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = 'n'
            if answer in ('n',''):
                print('Aborted.')
                return
            if answer == 'y':
                break
        result = cloud._request('POST','/api/client/prints',{'roomId':context['roomId'],
                                'participantId':context['participantId'],
                                'roomStartsAt':context['startsAt'],
                                'filename':source.name,'language':language,'code':code})
        print('Virtual print #' + str(result['id']) + ' created.')
        print('View your team print here: ' + (result.get('url') or context['serverUrl'] + '/vp/' + context['roomId'] + '/prints'))
    except (VPError, OSError) as exc:
        print('printfile: ' + str(exc), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
