"""The same explicit submission preview for every contest platform."""
from pathlib import Path


def confirm_submission(source: Path, config: dict, problem: str, language: str, url: str,
                       config_path: Path | None = None, *, assume_yes: bool = False) -> bool:
    from .cloud import check_submission
    check_submission(config)
    print('Submission information:')
    print(f'  filename:    {source}')
    if config_path is not None:
        print(f'  config:      {config_path}')
    print(f'  contest:     {config.get("name", "")}')
    print(f'  problem:     {problem}')
    print(f'  language:    {language}')
    print(f'  url:         {url}')
    if assume_yes:
        print('Do you want to continue? (y/n) y [-y]')
        return True
    while True:
        try:
            answer = input('Do you want to continue? (y/n) ').strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = 'n'
        if answer == 'y':
            return True
        if answer in ('n', ''):
            print('Aborted.')
            return False
