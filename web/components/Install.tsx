'use client';
import {useEffect,useRef,useState,type ReactNode} from 'react';
import {CopyButton,PageHeader} from './shared';
import {firstContestModes,guideSections,platformGuides,quickStartGuides,type GuideSection,type Platform} from './guideContent';
import styles from './Install.module.css';

const systems:Platform[]=['ubuntu','macos','windows'];
const imageDetails={
  browser:{file:'browser-extension.svg',alt:'在 Chrome 或 Edge 加载 XCPC 扩展并同步原站登录的操作示意',height:600},
  firefox:{file:'firefox-login.svg',alt:'使用普通 Firefox 登录并通过 xcpc doctor 检查登录的操作示意',height:515},
  folders:{file:'workspace-folders.svg',alt:'XCPC 保存根目录、比赛名称文件夹和源码文件的结构示意',height:560},
  terminal:{file:'terminal-dashboard.svg',alt:'比赛面板、下方终端及 submit 提交确认的示意',height:660},
  rooms:{file:'team-room-flow.svg',alt:'组队、接受邀请、队长参赛与全队查看历史记录的流程示意',height:585},
};

function RichText({text}:{text:string}){
  const parts=text.split(/(`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)]+\))/g);
  return <>{parts.map((part,index)=>{
    if(part.startsWith('`')&&part.endsWith('`'))return <code key={index}>{part.slice(1,-1)}</code>;
    const link=part.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/);
    return link?<a key={index} href={link[2]} target="_blank" rel="noreferrer">{link[1]} ↗</a>:part;
  })}</>;
}

function Command({command,label,note}:{command:string;label?:string;note?:string}){
  return <div className="guide-command-group">
    {label&&<p className="guide-command-label">{label}</p>}
    <div className="install-command"><pre><code>{command}</code></pre><CopyButton value={command} label="复制命令"/></div>
    {note&&<p className="install-note"><RichText text={note}/></p>}
  </div>;
}

function ExampleImage({kind,caption,platform}:{kind:NonNullable<GuideSection['image']>;caption?:string;platform:Platform}){
  const key=kind==='browser'&&platform==='ubuntu'?'firefox':kind;
  const image=imageDetails[key];
  return <figure className="guide-figure">
    <a href={'/guide/'+image.file} target="_blank" rel="noreferrer" aria-label={image.alt+'，查看大图'}>
      <img src={'/guide/'+image.file} alt={image.alt} width={1000} height={image.height} loading="lazy"/>
    </a>
    <figcaption><span>{caption||image.alt} <small>操作示意，使用示例数据。</small></span><a href={'/guide/'+image.file} target="_blank" rel="noreferrer">查看大图 ↗</a></figcaption>
  </figure>;
}

function Section({section,index,platform}:{section:GuideSection;index:number;platform:Platform}){
  const guide=platformGuides[platform];
  return <section className="card install-section guide-section" id={'guide-'+section.id}>
    <div className="section-heading"><h2><span className="guide-step-number">{index+1}</span>{section.title}</h2><a className="guide-section-link" href={'#guide-'+section.id} aria-label={'定位到'+section.title}>#</a></div>
    <div className="install-body">
      {section.intro&&<p className="guide-intro"><RichText text={section.intro}/></p>}
      {section.id==='install'&&<>
        <p>{guide.terminal}</p>
        <Command label={guide.label+' 一键安装命令'} command={guide.installCommand}/>
        <ul className="guide-list">{guide.setupNotes.map(note=><li key={note}><RichText text={note}/></li>)}</ul>
      </>}
      {section.paragraphs?.map((paragraph,index)=><p key={index}><RichText text={paragraph}/></p>)}
      {section.steps&&<ol className="install-steps">{section.steps.map((step,index)=><li key={index}><RichText text={step}/></li>)}</ol>}
      {section.platformNotes?.[platform]&&<div className="guide-system-notes"><h3>{guide.label} 操作说明</h3><ul className="guide-list">{section.platformNotes[platform]!.map((note,index)=><li key={index}><RichText text={note}/></li>)}</ul></div>}
      {section.id==='browser'&&guide.extensionPath&&<div className="guide-extension-path"><p className="guide-command-label">当前系统的扩展文件夹</p><div className="install-path"><code>{guide.extensionPath}</code><CopyButton value={guide.extensionPath} label="复制路径"/></div></div>}
      {section.commands?.map((entry,index)=>{
        const command=typeof entry.command==='string'?entry.command:entry.command[platform];
        return command?<Command key={index} label={entry.label} command={command} note={entry.note}/>:null;
      })}
      {section.table&&<div className="guide-table-wrap"><table className="guide-table"><thead><tr>{section.table.headers.map(header=><th key={header}>{header}</th>)}</tr></thead><tbody>{section.table.rows.map((row,index)=><tr key={index}>{row.map((cell,column)=><td key={column}><RichText text={cell}/></td>)}</tr>)}</tbody></table></div>}
      {section.image&&<ExampleImage kind={section.image} caption={section.imageCaption} platform={platform}/>}
    </div>
  </section>;
}

function QuickStep({number,title,children}:{number:number;title:string;children:ReactNode}){
  return <section className={'card guide-section '+styles.quickStep} id={'quick-step-'+number}>
    <div className="section-heading"><h2><span className="guide-step-number">{number}</span>{title}</h2></div>
    <div className="install-body">{children}</div>
  </section>;
}

function Picture({kind,platform}:{kind:NonNullable<GuideSection['image']>;platform:Platform}){
  return <details className={styles.picture}><summary>查看操作示意图</summary><ExampleImage kind={kind} platform={platform}/></details>;
}

function FirstContest({platform}:{platform:Platform}){
  const [mode,setMode]=useState<(typeof firstContestModes)[number]['command']>('bt');
  const [link,setLink]=useState('');
  const selected=firstContestModes.find(item=>item.command===mode)!;
  const value=link.trim();
  let valid=false,error='';
  try{
    const url=new URL(value),host=url.hostname.replace(/^www\./,'');
    const qoj=['qoj.ac','contest.ucup.ac'].includes(host)&&/^\/contest\/\d+\/?$/.test(url.pathname);
    const cf=host==='codeforces.com'&&/^\/(contest|gym)\/\d+\/?$/.test(url.pathname);
    const paa=host==='oj.cwnupaa.com'&&/^(\/d\/[A-Za-z0-9_.-]+)?\/contest\/[a-f0-9]{24}\/?$/i.test(url.pathname);
    const nk=host==='ac.nowcoder.com'&&/^\/acm\/contest\/[1-9]\d*\/?$/.test(url.pathname);
    valid=/^https?:$/.test(url.protocol)&&!url.port&&!url.username&&!url.password&&!/\s/.test(value)&&(qoj||cf||paa||nk);
    if(valid&&mode==='live'&&qoj){valid=false;error='QOJ 请选“虚拟参赛”或“赛后补题”。';}
    if(valid&&mode==='vp'&&(paa||nk)){valid=false;error='CWNUOJ 和牛客请选“实时比赛”或“赛后补题”。';}
  }catch{}
  // Quote a pasted URL for the selected shell, including '&' and ';'.
  const quoted=platform==='windows'?"'"+value.replace(/'/g,"''")+"'":"'"+value.replace(/'/g,"'\\''")+"'";
  return <>
    <p>选择你要做的事，再粘贴原站的比赛链接。</p>
    <div className={styles.modeChoices} role="group" aria-label="选择参赛方式">{firstContestModes.map(item=><button key={item.command} type="button" aria-pressed={mode===item.command} className={mode===item.command?styles.selected:''} onClick={()=>setMode(item.command)}>{item.label}<code>{item.command}</code></button>)}</div>
    <p className={styles.modeDescription}>{selected.description}</p>
    <label className={styles.urlLabel}>原站比赛链接<input type="url" value={link} onChange={event=>setLink(event.target.value)} placeholder="例如 https://qoj.ac/contest/3617" autoComplete="off" spellCheck={false}/></label>
    {valid?<Command label="复制到终端执行" command={mode+' '+quoted}/>:<p className="install-note">{error||'从比赛网页的地址栏复制完整链接，粘贴到这里后会生成命令。'}</p>}
    <p>打开比赛后，程序会自动建立比赛名称文件夹。用编辑器在里面新建、编写并保存 <code>A.cpp</code>，再提交：</p>
    <Command command="submit A.cpp" note="核对提交信息后输入 y 确认；submit A.cpp -y 可跳过确认输入。"/>
    <p>{platform==='windows'?'面板在当前终端显示。有下方命令区时直接输入；普通 PowerShell 先按 q 返回命令行，再提交。':'下方的命令区已经进入比赛文件夹，直接在这里输入提交命令。'}</p>
    <Picture kind="terminal" platform={platform}/>
  </>;
}

export default function Install(){
  const [platform,setPlatform]=useState<Platform>('ubuntu');
  const reference=useRef<HTMLDetailsElement>(null);
  const guide=platformGuides[platform];
  const quick=quickStartGuides[platform];
  useEffect(()=>{
    let frame=0;
    const reveal=()=>{
      const id=window.location.hash.slice(1);
      if(!guideSections.some(section=>'guide-'+section.id===id))return;
      if(reference.current)reference.current.open=true;
      cancelAnimationFrame(frame);
      frame=requestAnimationFrame(()=>document.getElementById(id)?.scrollIntoView({block:'start'}));
    };
    reveal();window.addEventListener('hashchange',reveal);
    return()=>{window.removeEventListener('hashchange',reveal);cancelAnimationFrame(frame);};
  },[]);
  return <div className="install-guide" id="guide-top">
    <PageHeader title="安装教程" description="选择你的电脑系统，按下面 5 步操作即可。"/>
    <div className="install-platforms" role="tablist" aria-label="选择操作系统">
      {systems.map(system=><button key={system} role="tab" aria-selected={platform===system} aria-controls="install-instructions" id={'install-tab-'+system} className={platform===system?'active':''} onClick={()=>setPlatform(system)}>{platformGuides[system].label}</button>)}
    </div>
    <div className={styles.quickGuide} role="tabpanel" id="install-instructions" aria-labelledby={'install-tab-'+platform}>
      <nav className={styles.quickNav} aria-label="安装步骤">{['安装','检查','登录','设置目录','开始使用'].map((title,index)=><a key={title} href={'#quick-step-'+(index+1)}><span>{index+1}</span>{title}</a>)}</nav>
      <QuickStep number={1} title="打开终端，复制安装命令">
        <p>{quick.openTerminal}</p>
        <Command label={guide.label+' 一键安装命令'} command={guide.installCommand}/>
        <p>无需提前安装 Python 或 Git，脚本会自动准备运行环境。</p>
      </QuickStep>
      <QuickStep number={2} title="等待完成，再检查是否安装成功">
        <p>{quick.authorization}</p>
        <p>安装完成后，关闭旧终端，重新打开终端并运行：</p>
        <Command command="xcpc --version"/>
        <p>能看到 <code>XCPC</code> 版本号就可以继续。{platform==='windows'?'不必另外安装 Windows Terminal，普通 PowerShell 也能使用。':''}</p>
      </QuickStep>
      <QuickStep number={3} title="登录比赛网站和本系统">
        <h3 className={styles.subheading}>{platform==='ubuntu'?'原站登录：使用 Firefox':'原站登录：加载 Chrome / Edge 扩展'}</h3>
        {platform!=='ubuntu'&&<Command command="xcpc doctor cf" note="先运行一次，准备扩展文件夹。此时提示未收到登录信息，可继续下面的步骤。"/>}
        <ol className="install-steps">{quick.browserSteps.map(step=><li key={step}>{step}</li>)}</ol>
        {guide.extensionPath&&<div className="install-path"><code>{guide.extensionPath}</code><CopyButton value={guide.extensionPath} label="复制路径"/></div>}
        <Picture kind="browser" platform={platform}/>
        {platform!=='ubuntu'&&<p>CF / Gym 提交时保持普通浏览器的 Codeforces 页面打开。更新 XCPC 后，在扩展管理页重新加载扩展并同步登录状态。</p>}
        <h3 className={styles.subheading}>登录本系统</h3>
        <p>运行下面的命令，输入协会官网账号和密码，用于保存云端历史和参加组队房间。</p>
        <Command command="xcpc login"/>
        <h3 className={styles.subheading}>Codeforces / Gym 用户还需要配置 API</h3>
        <p>打开 <a href="https://codeforces.com/settings/api" target="_blank" rel="noreferrer">Codeforces API 设置页 ↗</a> 创建 key 和 secret，再运行下面的命令，按提示输入用户名和密钥。使用其他原站可跳过。</p>
        <Command command="xcpc api cf"/>
      </QuickStep>
      <QuickStep number={4} title="设置代码保存目录">
        <p>复制下面的命令，将比赛和代码保存到自己用户目录下的 <code>XCPC</code> 文件夹。文件夹会自动创建。</p>
        <Command command={quick.folderCommand}/>
        <p>以后每场比赛都会在这里按比赛名称建立子文件夹。想换其他位置时，运行 <code>xcpc path</code> 按提示修改。</p>
        <Picture kind="folders" platform={platform}/>
      </QuickStep>
      <QuickStep number={5} title="打开第一场比赛并提交"><FirstContest platform={platform}/></QuickStep>
    </div>
    <details className={styles.reference} ref={reference}>
      <summary>详细使用与安装排错<span>更多命令、组队、目录位置、更新和常见问题</span></summary>
      <section className="card guide-overview" aria-label={guide.label+' 安装信息'}>
      <div className="section-heading"><h2>{guide.label} · 先了解文件存放位置</h2><span className="guide-overview-tag">程序共用 · 账号独立</span></div>
      <div className="install-body">
        <p>程序安装目录、个人配置目录与比赛保存目录是三种不同的位置。程序会建立所需的目录；你的源码应保存在比赛名称文件夹中。</p>
        <dl className="guide-path-grid">
          <div><dt>程序安装目录</dt><dd><code>{guide.programPath}</code></dd></div>
          <div><dt>全局命令目录</dt><dd><code>{guide.commandPath}</code></dd></div>
          <div><dt>个人账号与设置</dt><dd><code>{guide.configPath}</code></dd></div>
          <div><dt>个人比赛记录</dt><dd><code>{guide.statePath}</code></dd></div>
          <div><dt>比赛保存路径示例</dt><dd><code>{guide.workspaceExample}</code></dd></div>
          <div><dt>比赛详情窗口</dt><dd>{platform==='windows'?'当前终端；Windows Terminal 可分屏':'tmux 上下分屏'}</dd></div>
        </dl>
        <p className="install-note">默认比赛保存到桌面，可用 <code>xcpc path</code> 更改。示例路径中的 <code>player</code> 请替换成自己的电脑用户名，Windows 盘符按需修改；实际路径以 <code>xcpc --version</code> 为准。</p>
      </div>
      </section>
    <div className="guide-layout">
      <nav className="guide-index" aria-label="教程目录"><details open><summary>本页目录</summary><ol>{guideSections.map((section,index)=><li key={section.id}><a href={'#guide-'+section.id}><span>{index+1}</span>{section.title}</a></li>)}</ol></details><p>首次使用先完成安装、登录和目录设置，再选择 VP、live 或补题。</p></nav>
      <div className="install-content guide-content">
        {guideSections.map((section,index)=><Section key={section.id} section={section} index={index} platform={platform}/>)}
        <a className="guide-back-top" href="#guide-top">返回顶部 ↑</a>
      </div>
    </div>
    </details>
  </div>;
}
