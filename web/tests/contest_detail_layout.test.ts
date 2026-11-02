import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {ContestDetailView} from '../components/ContestDetail';
import type {History,Submission} from '../components/types';

function fixture(count=13):History {
  const problems=Array.from({length:count},(_,i)=>String.fromCharCode(65+i));
  const submissions:Submission[]=[
    {id:'396538368',problem:'A',submittedAt:'2026-10-31T01:02:03.000Z',verdict:'CORRECT',practice:false,url:'https://codeforces.com/contest/2277/submission/396538368',language:'GNU G++20'},
    {id:'3302287',problem:'B',submittedAt:'2026-10-31T01:03:04.000Z',verdict:'CORRECT',practice:true,url:'https://qoj.ac/submission/3302287'},
    {id:'0123456789abcdef01234567',problem:'C',submittedAt:'2026-10-31T01:04:05.000Z',verdict:'PENDING',practice:false,url:'https://oj.cwnupaa.com/record/0123456789abcdef01234567'},
    {id:'3302288',problem:'D',submittedAt:'2026-10-31T01:05:06.000Z',verdict:'WRONG-ANSWER',practice:false,url:'https://qoj.ac/submission/3302288'}
  ];
  return {
    id:'shared-detail-fixture',contestName:'The 2026 ICPC Fixture',contestUrl:'https://qoj.ac/contest/3617',
    mode:'vp',rank:'87',rankSource:'original',solved:1,penalty:62,scoring:'ICPC',problems,
    cells:Object.fromEntries(problems.map(problem=>[problem,{tries:0,pending:0,solved:false,practiceSolved:false}])),
    participantLabel:'Code-Zero-Alice、Bob、Carol',participantTitle:'Code-Zero',participantSubtitle:'Alice、Bob、Carol',participantType:'team',
    startedAt:'2026-10-31T01:00:00.000Z',endedAt:'2026-10-31T06:00:00.000Z',updatedAt:'2026-10-31T06:01:02.000Z',status:'complete',submissions,
    publicProblemStatistics:problems.map((problem,i)=>({label:problem,submitted:1000+i,attempted:900+i,accepted:800+i})),statisticsSource:'original'
  };
}
function render(history:History){return renderToStaticMarkup(createElement(ContestDetailView,{history}));}
function text(html:string){return html.replace(/<[^>]*>/g,'').replace(/&#x27;|&#39;/g,"'").replace(/&amp;/g,'&').replace(/&lt;/g,'<').replace(/&gt;/g,'>');}
function headers(html:string){return [...html.matchAll(/<th(?:\s[^>]*)?>([\s\S]*?)<\/th>/g)].map(match=>text(match[1]));}
function rowWith(html:string,id:string){return [...html.matchAll(/<tr(?:\s[^>]*)?>([\s\S]*?)<\/tr>/g)].map(match=>match[1]).find(row=>row.includes('#'+id))||'';}
function freeze<T>(value:T):T {
  if(value&&typeof value==='object'&&!Object.isFrozen(value)){
    for(const item of Object.values(value as Record<string,unknown>))freeze(item);
    Object.freeze(value);
  }
  return value;
}

test('detail submissions show ten newest records per page and paginate only above ten',()=>{
  for(const count of [0,10,11,23]){
    const history=fixture();
    history.submissions=Array.from({length:count},(_,i)=>({...history.submissions![0],id:String(1000+i),
      submittedAt:new Date(Date.UTC(2026,9,31,1,i)).toISOString()}));
    const html=render(history);
    const table=html.match(/<table class="data-table judge-submissions-table fit-table">([\s\S]*?)<\/table>/)?.[1]||'';
    assert.equal([...table.matchAll(/<tr[^>]*data-stage=/g)].length,Math.min(10,count));
    assert.equal(html.includes('aria-label="Submission pagination"'),count>10);
    if(count>10){
      assert(html.includes(`#${1000+count-1}`),'Newest submission appears first');
      assert(!rowWith(table,'1000'),'Older records wait for the next page');
      assert.match(html,/<button[^>]*disabled=""[^>]*aria-label="Previous page"/);
      assert.doesNotMatch(html,/<button[^>]*disabled=""[^>]*aria-label="Next page"/);
      assert(html.includes(`1–10 of ${count} submissions`));
    }
  }
});

test('history details and room My submissions use the shared detail renderer',()=>{
  for(const filename of ['History.tsx','Rooms.tsx']){
    const source=readFileSync(new URL('../components/'+filename,import.meta.url),'utf8');
    assert.match(source,/<ContestDetailView\s+history=/,filename);
  }
  assert.doesNotMatch(render(fixture()),/clarifications?/i);
});

test('room pages can supply their common title while keeping all shared detail content',()=>{
  const history=fixture(),html=renderToStaticMarkup(createElement(ContestDetailView,{history,showTitle:false}));
  assert.doesNotMatch(html,/<h1(?:\s[^>]*)?>/);
  assert.match(render(history),/<h1>The 2026 ICPC Fixture<\/h1>/);
  for(const heading of ['Rank','Team','Solved','Penalty','Time','Problem','Lang','Result','Submission'])assert(headers(html).includes(heading),heading);
  assert(html.includes(history.contestUrl));
  assert(html.includes('#396538368'));
});

test('the shared detail keeps every scoreboard header and both participant identity lines',()=>{
  for(const count of [13,20]){
    const history=fixture(count),html=render(history),labels=headers(html);
    for(const heading of ['Rank','Team','Solved','Penalty',...history.problems])assert(labels.includes(heading),heading+' must remain available');
    assert.match(html,/<strong>Code-Zero<\/strong>\s*<small>Alice、Bob、Carol<\/small>/);
    assert(html.includes('The 2026 ICPC Fixture'));
    assert(html.includes('87'),'Original official rank is retained');
    for(const label of ['public','submitted','attempted','accepted'])assert(labels.includes(label),label);
    for(const value of ['1000','900','800'])assert(text(html).includes(value),'Original public statistic '+value);
  }
  const personal=fixture();personal.participantType='individual';personal.participantTitle='nickname-with-hyphens';personal.participantSubtitle='Real Name';
  assert.match(render(personal),/<strong>nickname-with-hyphens<\/strong>\s*<small>Real Name<\/small>/);
});

test('detail records retain full timestamps, submission IDs, actual judging links and known or absent languages',()=>{
  const history=fixture(),html=render(history),labels=headers(html);
  for(const heading of ['Time','Problem','Lang','Result','Submission'])assert(labels.includes(heading),heading);
  for(const record of history.submissions!){
    const row=rowWith(html,record.id);
    assert(row,'Submission '+record.id+' remains visible');
    assert(row.includes(record.url!),record.id+' retains its original judging URL');
    assert.match(row,/target="_blank"/);assert.match(row,/rel="[^"]*noreferrer/);
    assert(row.includes(record.verdict));
  }
  for(const stamp of ['2026-10-31 09:02:03','2026-10-31 09:03:04','2026-10-31 09:04:05','2026-10-31 09:05:06'])assert(html.includes(stamp),stamp);
  assert.match(rowWith(html,'396538368'),/<td class="language-value">cpp<\/td>/);
  assert.match(rowWith(html,'3302287'),/<td class="language-value">other<\/td>/,'Missing language is grouped as other');
  const filter=[...html.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].map(match=>match[0]).find(button=>button.includes('Filter submissions'))||'';
  assert.match(filter,/aria-expanded="false"/);assert.match(filter,/aria-controls="[^"]+"/);
  assert.match(filter,/aria-label="Filter submissions"/);
});

test('native graded scores are displayed only when supplied and anonymous public counts stay unchanged',()=>{
  const history=fixture();history.scoring='IOI';history.points=75;
  history.cells.A={tries:1,pending:0,solved:false,practiceSolved:false,points:75,maxPoints:100};
  history.submissions![0]={...history.submissions![0],verdict:'PARTIAL',points:75};
  history.publicProblemStatistics=history.problems.map((_,i)=>({label:i===0?'C':null,submitted:i===0?7654:null,attempted:null,accepted:null}));
  const html=render(history),labels=headers(html),footer=html.match(/<tfoot>([\s\S]*?)<\/tfoot>/)?.[1]||'';
  assert(labels.includes('Score'));assert(html.includes('75'));
  assert(footer.includes('7654'));assert(!footer.includes('1000'));
  assert.match(footer,/<td[^>]*>\?<\/td>/);
  assert.match(footer,/<td[^>]*>—<\/td>/,'Unknown public statistics are not replaced with personal counts');
});

test('contest AC, practice AC, pending and incorrect keep distinct existing display states',()=>{
  const history=fixture();
  history.cells.A={tries:2,pending:0,solved:true,practiceSolved:true,solveTime:42};
  history.cells.B={tries:1,pending:0,solved:false,practiceSolved:true};
  history.cells.C={tries:1,pending:2,solved:false,practiceSolved:false};
  history.cells.D={tries:2,pending:0,solved:false,practiceSolved:false};
  const html=render(history);
  for(const state of ['accepted','upsolved','pending','rejected'])assert.match(html,new RegExp('class="problem-cell '+state+'"'));
  assert.match(html,/judge-verdict-accepted/);assert.match(html,/judge-verdict-pending/);assert.match(html,/judge-verdict-rejected/);
  assert.match(html,/judge-legend-upsolved/);assert.match(html,/judge-legend-pending/);
  assert(html.includes('2 + 2 tries')||html.includes('1 + 2 tries'),'Pending attempt count remains visible');
});

test('detail metadata stays in English and practice has no invented start or end time',()=>{
  const history=fixture(),html=render(history);
  const information=html.indexOf('class="judge-detail-information"');
  assert(information>html.indexOf('domjudge-submissions'),'Contest information follows the submission log in the shared two-column area');
  assert(html.indexOf('class="judge-identities"')>information,'VP and participation badges belong to the information panel');
  assert(html.indexOf('Original contest')>information,'The original link and timing stay with the right-hand information panel');
  for(const label of ['Original contest','Start:','End:','Last synced:','Cell colours'])assert(html.includes(label),label);
  assert(html.includes(history.contestUrl));
  assert(html.includes('2026-10-31 09:00:00'));assert(html.includes('2026-10-31 14:00:00'));assert(html.includes('2026-10-31 14:01:02'));
  for(const label of ['开始时间','结束时间','上次同步','摘要','图示'])assert(!html.includes(label),label);
  const practice=fixture();practice.mode='bt';practice.startedAt=null;practice.endedAt=null;
  const practiceHtml=render(practice);
  assert(!practiceHtml.includes('Start:'));assert(!practiceHtml.includes('End:'));
  assert(practiceHtml.includes('Last synced:'));assert(practiceHtml.includes('Original contest'));
});

test('rendering leaves all score data unchanged and safely escapes user-supplied text',()=>{
  const history=fixture(20);
  history.contestName='<script>alert("contest")</script>';
  history.participantTitle='<img src=x onerror="alert(1)">';
  history.participantSubtitle='Alice & Bob <captain>';
  history.submissions![0].language='<script>language</script>';
  const before=JSON.stringify(history);freeze(history);
  const html=render(history);
  assert.equal(JSON.stringify(history),before);
  assert.doesNotMatch(html,/<script>|<img src=x/);
  assert(html.includes('&lt;script&gt;'));assert(html.includes('&lt;img src=x'));
  assert(html.includes('Alice &amp; Bob &lt;captain&gt;'));
});
