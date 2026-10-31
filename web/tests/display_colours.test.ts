import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {Legend,ProblemCell} from '../components/shared';
import {ContestLegend} from '../components/ContestDetail';

test('practice is purple and pending or frozen is blue in cells and both legends',()=>{
  const css=readFileSync(new URL('../app/globals.css',import.meta.url),'utf8');
  assert.match(css,/--upsolve:#8b5cf6/);
  assert.match(css,/--pending:#2563eb/);
  assert.match(css,/\.judge-legend-upsolved\{background:var\(--upsolve\)/);
  assert.match(css,/\.judge-legend-pending\{background:var\(--pending\)/);
  assert.match(css,/\.judge-verdict-pending\{color:var\(--pending\)/);
  const general=renderToStaticMarkup(createElement(Legend));
  assert.match(general,/class="legend-blue"><\/i>Pending \/ frozen/);
  assert.match(general,/class="legend-purple"><\/i>Upsolved/);
  const detail=renderToStaticMarkup(createElement(ContestLegend));
  assert.match(detail,/judge-legend-upsolved">Upsolved/);
  assert.match(detail,/judge-legend-pending">Tried, pending/);
  const cell=(solved:boolean,practiceSolved:boolean,pending:number)=>renderToStaticMarkup(createElement(ProblemCell,{cell:{tries:1,pending,solved,practiceSolved}}));
  assert.match(cell(false,true,0),/problem-cell upsolved/);
  assert.match(cell(false,false,1),/problem-cell pending/);
  assert.match(cell(true,true,0),/problem-cell accepted/,'Existing contest AC stays green after practice');
});
