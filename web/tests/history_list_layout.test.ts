import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {HistoryResultsTable} from '../components/History';
import type {History} from '../components/types';

function fixture():History {
  const problems=Array.from({length:20},(_,i)=>String.fromCharCode(65+i));
  return {id:'complete-history-id',contestName:'A long official contest name & accepted team history',contestUrl:'https://qoj.ac/contest/3617',mode:'vp',rank:'87',rankSource:'original',solved:1,penalty:42,scoring:'ICPC',problems,participantLabel:'Team-Alice、Bob',participantTitle:'Team',participantSubtitle:'Alice、Bob',participantType:'team',startedAt:'2026-10-31T01:00:00.000Z',endedAt:'2026-10-31T06:00:00.000Z',updatedAt:'2026-10-31T06:01:02.000Z',cells:{A:{tries:2,pending:0,solved:true,practiceSolved:false,solveTime:42},B:{tries:1,pending:0,solved:false,practiceSolved:true},C:{tries:1,pending:2,solved:false,practiceSolved:false},D:{tries:3,pending:0,solved:false,practiceSolved:false}}};
}

test('history list keeps all metadata and 20 problem columns with original rank and two identity lines',()=>{
  const item=fixture(),before=JSON.stringify(item),html=renderToStaticMarkup(createElement(HistoryResultsTable,{items:[item],problems:item.problems}));
  const headings=[...html.matchAll(/<th\b[^>]*>([\s\S]*?)<\/th>/g)].map(match=>match[1].replace(/<[^>]+>/g,''));
  for(const label of ['Contest','Mode','Participation','Rank','Solved','Penalty / Score',...item.problems,'Details'])assert(headings.includes(label),label);
  assert.match(html,/<strong>Team<\/strong><small>Alice、Bob<\/small>/);
  assert(html.includes('href="/history/complete-history-id"'));
  assert.match(html,/<td class="rank-value">87<\/td>/);
  for(const state of ['accepted','upsolved','pending','rejected'])assert(html.includes('problem-cell '+state),state);
  for(const count of ['2 tries','1 try','1 + 2 tries','3 tries'])assert(html.includes(count),count);
  assert(html.includes('judge-problem-badge'));
  assert.equal(JSON.stringify(item),before);
});

test('history BT preserves attempt colours without manufacturing scored totals',()=>{
  const item=fixture();item.mode='bt';
  const html=renderToStaticMarkup(createElement(HistoryResultsTable,{items:[item],problems:item.problems}));
  assert.match(html,/<td class="metric-value">—<\/td><td>—<\/td>/);
  assert(html.includes('problem-cell upsolved'));
});

test('history search and type filter remain present without restoring the removed back link',()=>{
  const source=readFileSync(new URL('../components/History.tsx',import.meta.url),'utf8');
  for(const label of ['搜索比赛名称','筛选比赛类型','value="vp"','value="live"','value="bt"'])assert(source.includes(label),label);
  assert.doesNotMatch(source,/BackLink/);
});
