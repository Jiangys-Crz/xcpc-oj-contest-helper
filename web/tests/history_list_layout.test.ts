import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {HistoryLegend,HistoryResultsTable} from '../components/History';
import type {History} from '../components/types';

function fixture():History {
  const problems=Array.from({length:20},(_,i)=>String.fromCharCode(65+i));
  return {id:'complete-history-id',contestName:'A long official contest name & accepted team history',contestUrl:'https://qoj.ac/contest/3617',mode:'vp',rank:'87',rankSource:'original',solved:1,penalty:42,scoring:'ICPC',problems,participantLabel:'Team-Alice、Bob',participantTitle:'Team',participantSubtitle:'Alice、Bob',participantType:'team',startedAt:'2026-10-31T01:00:00.000Z',endedAt:'2026-10-31T06:00:00.000Z',updatedAt:'2026-10-31T06:01:02.000Z',cells:{A:{tries:2,pending:0,solved:true,practiceSolved:false,solveTime:42},B:{tries:1,pending:0,solved:false,practiceSolved:true},C:{tries:1,pending:2,solved:false,practiceSolved:false},D:{tries:3,pending:0,solved:false,practiceSolved:false}}};
}

const render=(...items:History[])=>renderToStaticMarkup(createElement(HistoryResultsTable,{items}));

test('history archive keeps contest metadata and all problem states without detailed result columns',()=>{
  const item=fixture(),before=JSON.stringify(item),html=render(item);
  const headings=[...html.matchAll(/<th\b[^>]*>([\s\S]*?)<\/th>/g)].map(match=>match[1].replace(/<[^>]+>/g,''));
  assert.deepEqual(headings,['比赛','题目概览','成绩概要']);
  for(const label of ['Team · Alice、Bob','虚拟参赛','ICPC','队伍','2026-10-31','排名 87'])assert(html.includes(label),label);
  assert(html.includes('href="/history/complete-history-id"'));
  for(const label of ['A · AC · 赛内通过','B · 补题通过','C · 待评测','D · RJ · 尝试未通过','T · 未尝试'])assert(html.includes('aria-label="'+label+'"'),label);
  assert(html.includes('赛内 AC 1 / 20 题'));
  assert(html.includes('+1 补题'));
  assert.doesNotMatch(html,/tries|1 try|Practice AC|Penalty|judge-detail-cell-content|table-fit-viewport/);
  assert.equal(JSON.stringify(item),before);
});

test('each contest shows only its own ordered problem strip, including long labels',()=>{
  const first=fixture(),second={...fixture(),id:'short-history',problems:['Ex','A1','100']};
  const html=render(first,second),rows=[...html.matchAll(/<tr>([\s\S]*?)<\/tr>/g)].slice(1).map(match=>match[1]);
  const labels=(row:string)=>[...row.matchAll(/role="img" aria-label="([^"·]+) ·/g)].map(match=>match[1]);
  assert.deepEqual(labels(rows[0]),first.problems);
  assert.deepEqual(labels(rows[1]),second.problems);
  assert(render({...first,problems:[],cells:{}}).includes('暂无题目'));
});

test('standalone practice counts practice AC without displaying competitive scores or rank',()=>{
  const item=fixture();item.mode='bt';item.scoring='IOI';item.points=175;item.prize='Gold';
  item.cells.A.practiceSolved=true;
  const html=render(item);
  assert(html.includes('补题通过 2 / 20 题'));
  assert(html.includes('A · 补题通过'));
  assert(html.includes('B · 补题通过'));
  assert.doesNotMatch(html,/赛内 AC|赛内总分|排名 87|金奖|\+2 补题/);
});

test('native scoring shows authoritative total points and distinguishes scored, zero and unknown problems',()=>{
  for(const scoring of ['OI','IOI','CF','WEEKLY']){
    const item=fixture();item.scoring=scoring;item.points=-60.5;
    // A CF AC can earn less than the maximum; a score alone never implies AC.
    item.cells.A={tries:1,pending:0,solved:true,practiceSolved:true,points:75,maxPoints:100};
    item.cells.B={tries:1,pending:0,solved:false,practiceSolved:false,points:40,maxPoints:100};
    item.cells.C={tries:1,pending:0,solved:false,practiceSolved:false,points:0,maxPoints:100};
    item.cells.D={tries:1,pending:1,solved:false,practiceSolved:false};
    item.cells.E={tries:0,pending:0,solved:false,practiceSolved:false,points:0,maxPoints:100};
    item.cells.F={tries:1,pending:0,solved:false,practiceSolved:true,points:25,maxPoints:100};
    const html=render(item);
    assert(html.includes('赛内总分：-60.5'),scoring);
    assert(html.includes('A · AC · 赛内通过 · 赛内得分 75 / 100'));
    assert(html.includes('B · 部分得分 · 赛内得分 40 / 100'));
    assert(html.includes('C · RJ · 尝试未通过 · 赛内得分 0 / 100'));
    assert(html.includes('D · 待评测'));
    assert(html.includes('E · 未尝试 · 赛内得分 0 / 100'));
    assert(html.includes('F · 补题通过 · 赛内得分 25 / 100'));
    assert(html.includes('1 / 20 AC'));assert(html.includes('+1 补题'));
    item.points=null;
    assert(render(item).includes('赛内总分：未知'),'Missing totals must not be summed or inferred from cells');
    item.points=0;
    assert(render(item).includes('赛内总分：0'));
  }
});

test('contest AC takes precedence over later practice and pending, and CCPC keeps solved counts',()=>{
  const item=fixture();item.scoring='CCPC';item.cells.A.practiceSolved=true;item.cells.A.pending=1;item.prize='Silver';
  const html=render(item);
  assert(html.includes('A · AC · 赛内通过'));
  assert(html.includes('赛内 AC 1 / 20 题'));
  assert(html.includes('+1 补题'));assert(html.includes('银奖'));
  assert.doesNotMatch(html,/赛内总分/);
});

test('the archive explains partial scores and pending results only when needed',()=>{
  const legend=(graded:boolean,pending=false)=>renderToStaticMarkup(createElement(HistoryLegend,{graded,pending}));
  assert(!legend(false).includes('部分得分'));
  assert(!legend(false).includes('待评测'));
  for(const label of ['AC · 赛内通过','RJ · 尝试未通过','补题通过','部分得分','待评测','未尝试'])assert(legend(true,true).includes(label),label);
  assert(!render({...fixture(),frozen:true}).includes('封榜'));
});

test('history search and type filter remain present without restoring the removed back link',()=>{
  const source=readFileSync(new URL('../components/History.tsx',import.meta.url),'utf8');
  for(const label of ['搜索比赛名称','筛选比赛类型','value="vp"','value="live"','value="bt"'])assert(source.includes(label),label);
  assert.doesNotMatch(source,/BackLink/);
});
