const fs = require('node:fs');
const path = require('node:path');
const root = __dirname;
const settings = JSON.parse(fs.readFileSync(path.join(root,'shared/env.json'),'utf8'));
module.exports = { apps: [{
  name:'xcpc-vp-web', script:path.join(root,'current/server.js'), cwd:path.join(root,'current'),
  interpreter:path.join(root,'runtime/node'), exec_mode:'fork', instances:1,
  node_args:'--max-old-space-size=384', max_memory_restart:'450M',
  env:{...settings,NODE_ENV:'production',HOSTNAME:'127.0.0.1',PORT:'3100',VP_DATA_DIR:path.join(root,'shared/data'),VP_RELEASE_MANIFEST:path.join(root,'shared/terminal-latest.json')},
  autorestart:true, exp_backoff_restart_delay:1000, kill_timeout:8000,
  out_file:path.join(root,'shared/logs/web-out.log'), error_file:path.join(root,'shared/logs/web-error.log'),
  merge_logs:true, log_date_format:'YYYY-MM-DD HH:mm:ss Z',
}] };
