from vp_helper.platform_clock import platform_now
from vp_helper.submission_results import print_judging_link
import sys
import time
import json
import re
import requests
from urllib.parse import urlsplit

from vp_helper.cf_forms import form_secrets, safe_url, sign_form, submission_error, submission_target, submission_headers
from vp_helper.models import VPError
from vp_helper.browser_rpc import BrowserRPCError

# cf_login.make_session may hand RoboBrowser a curl_cffi session instead of a
# requests one, so the session's own exception type must be caught too.
try:
    from curl_cffi.requests.errors import RequestsError as _CurlCffiError
except ImportError:  # pragma: no cover - curl_cffi not installed
    _CurlCffiError = requests.RequestException

_SESSION_ERRORS = (requests.RequestException, _CurlCffiError, BrowserRPCError)

import colours

""" submissions """
def get_submission_data(handle):
    req = requests.get("https://codeforces.com/api/user.status?handle={}&from=1&count=1".format(handle))
    content = req.content.decode()
    js = json.loads(content)
    if "status" not in js or js["status"] != "OK":
        print("Connection Error!")
    res = js["result"][0]
    # check if verdict exists (in queue if not)
    if "verdict" not in res:
        res["verdict"] = "IN QUEUE"
    return res["id"], res["problem"], res["verdict"], res["passedTestCount"], res["timeConsumedMillis"], res["memoryConsumedBytes"]

""" look at last submission """
def peek(handle):
    id_, probject, verdict, passedTests, timeCon, memCon = get_submission_data(handle)
    problem = str(probject["contestId"])+str(probject["index"])
    if verdict == "TESTING":
        print("submission "+str(id_) + " to problem "+problem + ": "+verdict)
    else:
        if verdict != "OK":
            print("submission "+str(id_) + " to problem "+problem + ": "+verdict + " on test "+str(1+passedTests) + "\n" + "Time: "+str(timeCon)+"ms \n" + "Memory: "+str(memCon/1024)+"Kb")
        else:
            print("submission "+str(id_) + " to problem " + problem + ": " + verdict+"! passed all " + str(passedTests) + " tests\n" + "Time: "+str(timeCon)+"ms \n" + "Memory: "+str(memCon/1024)+"Kb")

""" watch last submission """
def watch(handle):
    spinner = { 0: "-", 1: "/", 2: "\\" }
    count = 0
    while True:
        id_, probject, verdict, passedTests, timeCon, memCon = get_submission_data(handle)
        if "contestId" not in probject:
            probject["contestId"] = "guru"
        problem = str(probject["contestId"])+str(probject["index"])
        if verdict != "TESTING" and verdict != "IN QUEUE":
            if verdict != "OK":
                sys.stdout.write("\r" + "submission "+str(id_) + " to problem " + problem + ": " + verdict + " on test "+str(1+passedTests) + "\n" + "Time: "+str(timeCon)+"ms \n" + "Memory: "+str(memCon/1024)+"Kb")
            else:
                sys.stdout.write("\r" + "submission "+str(id_) + " to problem " + problem + ": " + verdict+"! passed all " + str(passedTests) + " tests\n" + "Time: "+str(timeCon)+"ms \n" + "Memory: "+str(memCon/1024)+"Kb")
            sys.stdout.flush()
            break
        else:
            sys.stdout.write("\r" + "submission "+str(id_) + " to problem "+problem + ": "+verdict + " ....... "+spinner[count] + (' '*7))
            sys.stdout.flush()
            count = (count+1) % 3
        time.sleep(0.25)
    print('\a')


""" submit problem """
def submit_problem(browser, contest, lang, source, guru, expected_problem=None):
    prefix = 'gym' if len(contest) >= 6 else 'contest'
    base_url = 'https://codeforces.com/' + prefix + '/' + contest
    native_base = 'https://codeforces.com/problemsets/acmsguru' if guru != -1 else base_url
    page_url = browser.url
    native_form = browser.parsed.select_one('form.submit-form')
    if native_form is not None:
        if native_form.get('method', 'get').lower() != 'post':
            raise VPError('Codeforces did not offer a POST submission form. No code was submitted.')
        sign_form(browser.parsed, native_form, browser.session, page_url)
        csrf = native_form.select_one('[name="csrf_token"]')
        if csrf is None or not csrf.get('value'):
            raise VPError('Codeforces did not provide its submission CSRF token. No code was submitted.')
    """ get form """
    submission = browser.get_form(class_="submit-form")
    if submission is None:
        if guru == -1:
            print_judging_link(base_url)
        raise VPError(submission_error(browser.parsed, getattr(browser.response, 'status_code', None), browser.url))

    """ submit form """
    langcode = None
    if lang == "cpp":
        # GNU G++20 11.2.0 (64 bit)
        langcode = "73"
        # GNU G++17 9.2.0 (64 bit)
        # langcode = "61"
        # GNU G++17 7.3.0
        # langcode = "54"
        # GNU G++14 6.2.0
        # langcode = "50"
        # GNU G++11 5.1.0
        # langcode = "42"
    #elif lang == "c++17":
        # GNU G++17 7.3.0
        # langcode = "54"
    elif lang == "c":
        # GNU GCC C11 5.1.0
        langcode = "43"
    elif lang == "d":
        langcode = "28"
    elif lang == "py":
        # python 2.7
        # langcode = "7"
        # python 3.6
        #langcode = "31"
    #elif lang == "pypy":
        # pypy 3.5
        langcode = "41"
    elif lang == "rb":
        # Ruby 2.0.0p645
        langcode = "8"
    elif lang == "java":
        # Java 1.8.0_112
        # langcode = "36"
        # Java 11.0.6
        langcode = "60"
    elif lang == "scala":
        langcode = "20"
    elif lang == "rs":
        langcode = "49"
    elif lang == "php":
        langcode = "6"
    elif lang == "go":
        langcode = "32"
    elif lang == "text":
        langcode = "57"
    elif lang == "kt":
        langcode = "48"
    else:
        print("Unknown Language")
        return False
    field = submission["programTypeId"]
    available = {str(value): str(label) for value, label in zip(field.options, field.labels)}
    preferred = ("GNU G++20", "GNU G++23", "GNU G++17") if lang == "cpp" else ("PyPy 3", "Python 3") if lang == "py" else ()
    if langcode not in available or lang == "py":
        langcode = next((value for prefix in preferred for value, label in available.items()
                         if label.startswith(prefix)), None)
    if langcode is None:
        print("No compatible compiler is offered by this contest")
        return False
    submission["programTypeId"] = langcode

    # check acmsguru
    if guru != -1:
        submission["submittedProblemCode"] = guru
    elif expected_problem is not None:
        field = submission['submittedProblemIndex']
        options = getattr(field, 'options', None)
        if isinstance(options, (list, tuple, dict)) and expected_problem.upper() not in options:
            raise VPError('This problem is not offered by the native Codeforces form. No code was submitted.')
        submission['submittedProblemIndex'] = expected_problem.upper()

    from pathlib import Path
    from vp_helper.submission_confirmation import confirm_submission
    config_path = Path('contest.json')
    config = json.loads(config_path.read_text(encoding='utf8')) if config_path.is_file() else {'name': contest}
    problem = str(guru) if guru != -1 else str(submission['submittedProblemIndex'].value)
    action = native_form.get('action', '') if native_form is not None else ''
    target = submission_target(page_url, action, native_base, problem if guru == -1 else None)
    submission.action = target
    secrets = form_secrets(submission, browser.session, target)
    confirmation = {'assume_yes': True} if getattr(browser, 'xcpc_assume_yes', False) is True else {}
    if not confirm_submission(Path(source), config, problem, available[langcode], safe_url(target),
                              config_path if config_path.is_file() else None, **confirmation):
        return False
    browser.vp_submitted_at = platform_now(base_url)
    try:
        # FileInput otherwise opens text with the system locale (e.g. GBK on
        # Windows). Upload the original UTF-8 bytes and close them on all paths.
        if native_form is None or native_form.select_one('[name="sourceFile"]') is not None:
            with Path(source).open('rb') as source_file:
                submission['sourceFile'] = source_file
                browser.submit_form(submission, timeout=(10, 40), headers=submission_headers(page_url, target))
        elif native_form.select_one('[name="source"]') is not None:
            submission['source'] = Path(source).read_text(encoding='utf8')
            browser.submit_form(submission, timeout=(10, 40), headers=submission_headers(page_url, target))
        else:
            raise VPError('Codeforces did not offer a source field. No code was submitted.')
    except _SESSION_ERRORS as exc:
        if guru == -1:
            print_judging_link(base_url)
        if isinstance(exc, BrowserRPCError) and not exc.outcome_unknown:
            raise VPError(str(exc)) from None
        raise VPError('Submission outcome unknown. Check My submissions before retrying; the request was not repeated.') from None

    """ check if good """
    result = urlsplit(browser.url)
    status = getattr(browser.response, 'status_code', None)
    accepted_path = urlsplit(native_base).path.rstrip('/') + ('/status' if guru != -1 else '/my')
    if (result.scheme != 'https' or result.hostname not in ('codeforces.com', 'www.codeforces.com')
            or result.path.rstrip('/') != accepted_path or isinstance(status, int) and status != 200):
        if guru == -1:
            print_judging_link(base_url)
        raise VPError(submission_error(browser.parsed, status, browser.url, secrets,
                                      headers=getattr(browser.response, 'headers', None), attempted=True))
    print("Code submitted properly")

    """ now get time """
    countdown_timer = browser.parsed.find_all("span", class_="contest-state-regular countdown before-contest-"+contest+"-finish")
    if len(countdown_timer) > 0:
        print(colours.bold() + "TIME LEFT: " + str(countdown_timer[0].get_text(strip=True)) + colours.reset())

    return True


""" submit problem """
def submit(browser, handle, contest, problem, lang, source, show, guru):
    from pathlib import Path
    import json
    from vp_helper.receipts import cf_submission_id, cf_submission_ids, record_receipt
    from vp_helper.models import VPError
    previous = None
    if not guru:
        prefix = 'gym' if len(contest) >= 6 else 'contest'
        try:
            response = browser.session.get('https://codeforces.com/' + prefix + '/' + contest + '/my', timeout=(10, 40))
            if response.status_code == 200 and response.url.split('?')[0].endswith('/' + contest + '/my'):
                previous = cf_submission_ids(response.text)
        except _SESSION_ERRORS:
            pass
    if guru:
        print("Submitting to acmsguru " + problem + " as " + handle)
    else:
        print("Submitting to problem " + contest + problem.upper() + " as " + handle)

    pid = -1
    if guru:
        browser.open("https://codeforces.com/problemsets/acmsguru/submit")
        pid = problem
    elif len(contest) >= 6:
        browser.open("https://codeforces.com/gym/" + contest + "/submit/" + problem.upper())
    else:
        browser.open("https://codeforces.com/contest/" + contest + "/submit/" + problem.upper())

    """ show submission """
    submitted_at = platform_now(browser.url)
    if submit_problem(browser, contest, lang, source, pid, expected_problem=problem):
        submitted_at = browser.vp_submitted_at
        sid = None
        if previous is not None:
            sid = cf_submission_id(browser.parsed, previous, contest, problem, handle)
            if sid is not None:
                print('Submission #' + sid + ': PENDING', flush=True)
                config = Path('contest.json')
                if config.is_file():
                    try:
                        record_receipt(json.loads(config.read_text(encoding='utf8')), sid, problem.upper(), submitted_at)
                    except (VPError, OSError) as exc:
                        print('Submitted, but the monitor notification failed: ' + str(exc))
        if not guru:
            print_judging_link('https://codeforces.com/' + prefix + '/' + contest, sid)
        if show:
            watch(handle)

""" submit, possibly len(args) > 1 """
def submit_files(browser, defaulthandle, defaultcontest, defaultprob, defext, defaultlang, args, show, guru):
    """ if len == 0, query for file """
    if len(args) == 0:
        args.append(raw_input("File to submit: "))

    for source in args:
        """ split file name """
        info = source.split('.')
        """ single filename """
        if source.find('.') == -1:
            info.append(defext)
            source += "." + defext

        """ check language """
        if defaultlang is not None:
            info[-1] = defaultlang

        """ submit problem """
        if defaultprob is not None:
            if len(defaultprob) == 1:
                """ letter only """
                submit(browser, defaulthandle, defaultcontest, defaultprob, info[-1], source, show, guru)
            elif len(defaultprob) == 2:
                """ letter + number (?) """
                submit(browser, defaulthandle, defaultcontest, defaultprob, info[-1], source, show, guru)
            else:
                """  parse string """
                splitted = re.split(r'(\D+)', defaultprob)
                if len(splitted) == 3 and len(splitted[1]) == 1 and len(splitted[2]) == 0:
                    """ probably a good string """
                    submit(browser, defaulthandle, splitted[0], splitted[1], info[-1], source, show, guru)
                else:
                    print("cannot understand the problem specified")

        elif len(info) == 2:
            if guru:
                """ ACMSGURU """
                submit(browser, defaulthandle, defaultcontest, info[0], info[1], source, show, guru)
            else:
                """ CODEFORCES """
                """ try to parse info[0] """
                if info[0][:2].lower() == "cf":
                    """ remove the cf """
                    info[0] = info[0][2:]
                if len(info[0]) == 1:
                    """ only the letter, use default contest """
                    submit(browser, defaulthandle, defaultcontest, info[0], info[1], source, show, guru)
                else:
                    """ contest is included, so parse """
                    splitted = re.split(r'(\D+)', info[0])
                    if len(splitted) == 3 and len(splitted[1]) == 1 and len(splitted[2]) == 0:
                        """ probably good string ? """
                        submit(browser, defaulthandle, splitted[0], splitted[1], info[1], source, show, guru)
                    else:
                        print("cannot parse filename, specify problem with -p or --prob")

        else:
            print("cannot parse filename, specify problem with -p or --prob")
