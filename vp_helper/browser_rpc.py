"""Bounded Codeforces requests through an existing ordinary browser profile.

Jobs are local, profile-bound and claimed once. A lost POST response never
permits a retry through this transport or a different HTTP client.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from uuid import uuid4

from .models import VPError
from .storage import file_lock, read_json, state_directory, write_json

CAPABILITY = 'cf-request-v1'
MAX_REQUEST = 512 * 1024
MAX_RESPONSE = 512 * 1024
DEFAULT_TIMEOUT = 45.0
_PROFILE = re.compile(r'[a-f0-9-]{32,36}')
_ID = re.compile(r'[a-f0-9]{32}')
_FIELDS = {'csrf_token','ftaa','bfaa','_tta','action','contestId','submittedProblemIndex','programTypeId','source','sourceFile','tabSize','sourceCodeConfirmed','captcha','cf-turnstile-response','g-recaptcha-response'}


class BrowserRPCUnavailable(VPError):
    """No request was dispatched; update/reload the ordinary-profile extension."""


class BrowserRPCError(VPError):
    def __init__(self, message, *, outcome_unknown=False):
        super().__init__(message)
        self.outcome_unknown = outcome_unknown


def request_timeout(value=None):
    """Accept requests/RoboBrowser defaults while keeping local jobs bounded."""
    if value is None:
        return DEFAULT_TIMEOUT
    parts = value if isinstance(value, (tuple, list)) else (value,)
    if isinstance(value, (tuple, list)) and len(parts) != 2:
        raise BrowserRPCError('The browser request timeout is invalid. No request was sent.')
    seconds = 0.0
    for part in parts:
        if part is None:
            part = DEFAULT_TIMEOUT
        if isinstance(part, bool) or not isinstance(part, (int, float)):
            raise BrowserRPCError('The browser request timeout is invalid. No request was sent.')
        try:
            number = float(part)
        except (OverflowError, ValueError):
            raise BrowserRPCError('The browser request timeout is invalid. No request was sent.') from None
        if not math.isfinite(number) or number <= 0:
            raise BrowserRPCError('The browser request timeout is invalid. No request was sent.')
        seconds += min(90.0, number)
    return min(90.0, max(1.0, seconds))


def validate_target(method, url):
    method = str(method).upper()
    try:
        p = urlsplit(url)
        if p.scheme != 'https' or p.hostname not in ('codeforces.com','www.codeforces.com') or p.username or p.password or p.port or p.fragment:
            raise ValueError
        common = re.fullmatch(r'/(contest|gym)/[0-9]+(?:/(?:my|status|submit(?:/[A-Za-z][0-9]*)?))?/?',p.path)
        if method == 'GET':
            if p.path not in ('/','/enter') and not common and not re.fullmatch(r'/submission/[0-9]+/?',p.path):
                raise ValueError
            from urllib.parse import parse_qsl
            if any(k not in ('locale','mobile','csrf_token','prevId','page') or len(v)>256 for k,v in parse_qsl(p.query,keep_blank_values=True)):
                raise ValueError
        elif method == 'POST':
            if not re.fullmatch(r'/(contest|gym)/[0-9]+/submit(?:/[A-Za-z][0-9]*)?/?',p.path):
                raise ValueError
            from urllib.parse import parse_qsl
            if any(k != 'csrf_token' for k,_ in parse_qsl(p.query,keep_blank_values=True)):
                raise ValueError
        else:
            raise ValueError
    except (TypeError,ValueError):
        raise BrowserRPCError('The browser request destination is not allowed. No request was sent.') from None
    return method,url


def _identity(source, profile):
    if source not in ('chrome','edge') or not isinstance(profile,str) or not _PROFILE.fullmatch(profile):
        raise ValueError('Invalid browser profile')
    return state_directory()/'browser-rpc'/source/profile


def _selection(account):
    source = account.get('cookie_source')
    path = Path(account.get('browser_profile') or '')
    expected = state_directory()/'browser-login'/str(source)
    if source not in ('chrome','edge') or path.suffix != '.json' or path.resolve().parent != expected.resolve() or not _PROFILE.fullmatch(path.stem):
        raise BrowserRPCUnavailable('Reload the updated XCPC extension in the logged-in Chrome / Edge profile, then sync its login.')
    snapshot=read_json(path)
    if not isinstance(snapshot,dict):
        raise BrowserRPCUnavailable('Sync the updated XCPC extension from the configured ordinary browser profile.')
    if snapshot.get('source') != source or snapshot.get('profile_id') != path.stem or CAPABILITY not in snapshot.get('capabilities',[]):
        raise BrowserRPCUnavailable('Reload the updated XCPC extension at chrome://extensions or edge://extensions, then sync its login.')
    if time.time()-snapshot.get('updated_at',0)>120:
        raise BrowserRPCUnavailable('Open the configured ordinary Chrome / Edge profile and sync the XCPC extension before submitting.')
    return source,path.stem


def available(account):
    try:
        _selection(account)
        return True
    except (VPError,OSError,ValueError,TypeError):
        return False


@dataclass
class BrowserResponse:
    status_code: int
    url: str
    text: str = field(repr=False)
    headers: dict
    cookies: object = field(default=None,repr=False)
    encoding: str = 'utf8'
    history: tuple = ()

    @property
    def content(self):
        return self.text.encode('utf8')

    def json(self):
        return json.loads(self.text)

    @property
    def ok(self):
        return self.status_code < 400

    def raise_for_status(self):
        if not self.ok:
            import requests
            raise requests.HTTPError('Codeforces browser request returned HTTP '+str(self.status_code),response=self)


def request(account, method, url, *, data=None, files=None, timeout=45):
    method,url=validate_target(method,url)
    lifetime=request_timeout(timeout)
    source,profile=_selection(account)
    handle=account.get('handle','')
    if not isinstance(handle,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{0,64}' if method=='GET' else r'[A-Za-z0-9_.-]{1,64}',handle):
        raise BrowserRPCUnavailable('Configure the Codeforces handle before using the browser connection.')
    fields={}
    for key,value in (data or {}).items():
        if key not in _FIELDS or not isinstance(value,(str,int,list,tuple)):
            raise BrowserRPCError('The browser submission form contains unsupported fields. No request was sent.')
        values=value if isinstance(value,(list,tuple)) else [value]
        if any(not isinstance(v,(str,int)) for v in values):
            raise BrowserRPCError('The browser submission form contains unsupported values. No request was sent.')
        fields[key]=[str(v) for v in values]
    upload=None
    if files:
        if set(files)!={'sourceFile'} or not isinstance(files['sourceFile'],tuple) or len(files['sourceFile'])<2:
            raise BrowserRPCError('The browser upload must contain one source file. No request was sent.')
        filename,body=files['sourceFile'][:2]
        if hasattr(body,'read'):
            body=body.read(MAX_REQUEST+1)
        if isinstance(body,bytes):
            try:
                body=body.decode('utf8')
            except UnicodeDecodeError:
                raise BrowserRPCError('The source file must use UTF-8. No request was sent.') from None
        if not isinstance(body,str) or not isinstance(filename,str) or re.search(r'[\x00-\x1f\x7f/\\]',filename) or not filename or len(filename)>200:
            raise BrowserRPCError('The browser source file is invalid. No request was sent.')
        upload={'name':filename,'content':body}
    if method=='GET' and (fields or upload):
        raise BrowserRPCError('GET browser requests cannot include submission data.')
    if method=='POST':
        parts=urlsplit(url).path.strip('/').split('/')
        if fields.get('contestId',[parts[1]])!=[parts[1]] or len(parts)==4 and fields.get('submittedProblemIndex')!=[parts[3].upper()]:
            raise BrowserRPCError('The browser form targets a different contest or problem. No request was sent.')
    job={'id':uuid4().hex,'source':source,'profile_id':profile,'method':method,'url':url,'data':fields,'file':upload,
         'expected_handle':handle,'created_at':time.time(),'expires_at':time.time()+lifetime,'status':'pending'}
    if len(json.dumps(job,ensure_ascii=False).encode('utf8'))>MAX_REQUEST:
        raise BrowserRPCError('The browser request is too large. No request was sent.')
    root=_identity(source,profile);path=root/(job['id']+'.json')
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    write_json(path,job)
    deadline=time.monotonic()+lifetime
    try:
        while time.monotonic()<deadline:
            saved=read_json(path)
            if saved.get('status')=='done':
                result=saved.get('result',{})
                if result.get('error'):
                    raise BrowserRPCError(result['error'],outcome_unknown=bool(result.get('outcome_unknown')))
                import requests
                jar=requests.cookies.RequestsCookieJar()
                for c in result.get('cookies',[]):
                    jar.set(c['name'],c['value'],domain=c['domain'],path='/')
                return BrowserResponse(result['status'],result['url'],result['text'],result['headers'],jar)
            time.sleep(.1)
        with file_lock(root/'queue.lock'):
            saved=read_json(path)
            claimed=saved.get('status') in ('claimed','done')
            saved.update(status='expired',data={},file=None)
            write_json(path,saved)
        unknown=method=='POST' and claimed
        raise BrowserRPCError('Browser submission outcome unknown. Check Codeforces My submissions before retrying; the request was not repeated.' if unknown else 'The browser did not complete this request. Keep a normal Codeforces tab open in the configured profile.',outcome_unknown=unknown)
    finally:
        # Claimed requests are never made pending again, even on interruption.
        with file_lock(root/'queue.lock'):
            current=read_json(path)
            if current.get('status')=='claimed':
                current.update(status='expired',data={},file=None)
                write_json(path,current)
            elif current.get('status') in ('done','pending','expired'):
                path.unlink(missing_ok=True)


def poll(source,profile):
    root=_identity(source,profile)
    with file_lock(root/'queue.lock'):
        paths=sorted(root.glob('*.json'))
        for path in paths:
            value=read_json(path)
            if value.get('expires_at',0)<=time.time() and value.get('status') in ('claimed','done','expired'):
                path.unlink(missing_ok=True)
                continue
            if value.get('status')=='claimed' and value.get('expires_at',0)>time.time():
                return None
        for path in paths:
            value=read_json(path)
            if value.get('status')!='pending':
                continue
            if value.get('expires_at',0)<=time.time():
                path.unlink(missing_ok=True);continue
            if value.get('source')!=source or value.get('profile_id')!=profile:
                continue
            validate_target(value.get('method'),value.get('url'))
            value['status']='claimed'
            write_json(path,value)
            return value
    return None


def complete(source,profile,job_id,result):
    root=_identity(source,profile)
    if not isinstance(job_id,str) or not _ID.fullmatch(job_id) or not isinstance(result,dict):
        raise ValueError('Invalid browser response')
    with file_lock(root/'queue.lock'):
        path=root/(job_id+'.json');job=read_json(path)
        if job.get('status')!='claimed' or job.get('source')!=source or job.get('profile_id')!=profile:
            return
        if result.get('error'):
            errors={'no-tab':'Keep an ordinary Codeforces tab open in the configured Chrome / Edge profile.',
                    'account-mismatch':'The ordinary Codeforces tab is logged into another account. No code was submitted.',
                    'verification':'Complete browser verification in your normal Codeforces tab before retrying.',
                    'expired':'The browser request expired before sending. Run submit again to confirm a fresh request.',
                    'browser-error':'The browser request did not finish. Check My submissions before retrying.',
                    'too-large':'The browser response exceeded the supported size. Check My submissions before retrying.'}
            safe={'error':errors.get(result['error'],errors['browser-error']),'outcome_unknown':job['method']=='POST' and result.get('sent') is True}
        else:
            from urllib.parse import urlunsplit
            parts=urlsplit(result.get('url'))
            validate_target('GET',urlunsplit((parts.scheme,parts.netloc,parts.path,'','')))
            status=result.get('status');body=result.get('text')
            if type(status) is not int or not 100<=status<=599 or not isinstance(body,str) or len(body.encode('utf8'))>MAX_RESPONSE:
                raise ValueError('Invalid browser response')
            headers={k:v for k,v in (result.get('headers') or {}).items() if k.lower() in ('content-type','date') and isinstance(v,str) and len(v)<1000}
            safe={'status':status,'url':result['url'],'text':body,'headers':headers,'cookies':[]}
            from .browser_bridge import snapshot_cookies
            safe['cookies']=snapshot_cookies({'cookies':result.get('cookies',[])},cf_only=True)
            if len(json.dumps(safe['cookies'],ensure_ascii=False).encode('utf8'))>65536:
                raise ValueError('Browser cookie metadata exceeded the supported size')
        job.update(status='done',data={},file=None,result=safe)
        write_json(path,job)
