'use client';
import {useState} from 'react';
import {CopyButton,PageHeader} from './shared';
import {guideSections,platformGuides,type GuideSection,type Platform} from './guideContent';

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

export default function Install(){
  const [platform,setPlatform]=useState<Platform>('ubuntu');
  const guide=platformGuides[platform];
  return <div className="install-guide" id="guide-top">
    <PageHeader title="安装与使用教程" description="选择你的系统，从安装、登录和建立比赛文件夹开始，完成第一次参赛与提交。"/>
    <div className="install-platforms" role="tablist" aria-label="选择操作系统">
      {systems.map(system=><button key={system} role="tab" aria-selected={platform===system} aria-controls="install-instructions" id={'install-tab-'+system} className={platform===system?'active':''} onClick={()=>setPlatform(system)}>{platformGuides[system].label}</button>)}
    </div>
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
          <div><dt>比赛详情窗口</dt><dd>{platform==='windows'?'Windows Terminal 原生分屏':'tmux 上下分屏'}</dd></div>
        </dl>
        <p className="install-note">默认比赛保存到桌面，可用 <code>xcpc path</code> 更改。示例路径中的 <code>player</code> 请替换成自己的电脑用户名，Windows 盘符按需修改；实际路径以 <code>xcpc --version</code> 为准。</p>
      </div>
    </section>
    <div className="guide-layout">
      <nav className="guide-index" aria-label="教程目录"><details open><summary>本页目录</summary><ol>{guideSections.map((section,index)=><li key={section.id}><a href={'#guide-'+section.id}><span>{index+1}</span>{section.title}</a></li>)}</ol></details><p>首次使用先完成安装、登录和目录设置，再选择 VP、live 或补题。</p></nav>
      <div className="install-content guide-content" role="tabpanel" id="install-instructions" aria-labelledby={'install-tab-'+platform}>
        {guideSections.map((section,index)=><Section key={section.id} section={section} index={index} platform={platform}/>)}
        <a className="guide-back-top" href="#guide-top">返回顶部 ↑</a>
      </div>
    </div>
  </div>;
}
