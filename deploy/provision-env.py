#!/usr/bin/env python3
"""Send runtime secrets through encrypted SSH stdin; never store them in source."""
import getpass,json,subprocess,os
settings={'VP_ADMIN_ACCOUNT':'admin','VP_ADMIN_PASSWORD':getpass.getpass('Initial admin password: '),
          'VP_EXTERNAL_APP_KEY':getpass.getpass('Association appKey: '),
          'VP_EXTERNAL_BASE_URL':'https://www.cwnupaa.com/api','VP_PUBLIC_URL':'https://vp.cwnupaa.com'}
if not all(settings.values()): raise SystemExit('Both credentials are required.')
remote="""import json,os,sys,pwd,pathlib
p=pathlib.Path('/home/paa/xcpc-vp-system/shared/env.json')
p.parent.mkdir(parents=True,exist_ok=True)
x=json.load(sys.stdin)
p.write_text(json.dumps(x),encoding='utf8')
u=pwd.getpwnam('xcpc-vp'); os.chown(p,0,u.pw_gid); p.chmod(0o640)
print('Runtime credentials configured; values were not printed.')
"""
import shlex
subprocess.run(['ssh','-S',os.environ.get('XCPC_SSH_CONTROL','/tmp/xcpc-vp-deploy-20261030.sock'),'root@43.136.170.90','python3 -c '+shlex.quote(remote)],input=json.dumps(settings).encode(),check=True)
