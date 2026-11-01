import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {submissionLanguage} from '../lib/submission-language';
import {SubmissionsTable} from '../components/ContestDetail';
import type {Submission} from '../components/types';

const samples={
  c:['c','C','C11','C (GCC 10)','GNU GCC C11 5.1.0','GNU C17','c.gcc'],
  cpp:['cpp','C++','GNU G++20 13.2 (64 bit, winlibs)','C++17 (GCC 9)','Clang++20','C++（clang++18）','cc.cc20o2','Microsoft Visual C++ 2017','Ｃ＋＋２０'],
  py:['py','Python','Python 3.12','Python3','PyPy 3','PyPy3 (7.3)','py.pypy3'],
  java:['java','Java 8','Java21','java.java17','Java (OpenJDK 21)'],
  other:['Rust 1.75','C# 12','Mono C#','JavaScript V8','Kotlin','Go 1.22','54','.cpp','file.py','unknown','']
};

test('native compiler labels consistently group into five requested language categories',()=>{
  for(const [category,labels] of Object.entries(samples))for(const label of labels)assert.equal(submissionLanguage(label),category,label);
  for(const value of [null,undefined,54,{},['C++'],true,'x'.repeat(201)])assert.equal(submissionLanguage(value),'other');
});

test('the shared history and room submission table displays only categories, including unknown legacy records',()=>{
  const records:Submission[]=['GNU C11','GNU G++20','PyPy 3','Java 21','Rust 1.75',undefined].map((language,index)=>({id:String(index+1),problem:'A',submittedAt:'2026-11-01T01:02:03.000Z',verdict:'CORRECT',practice:false,language,url:'https://qoj.ac/submission/'+(index+1)}));
  const before=JSON.stringify(records),html=renderToStaticMarkup(createElement(SubmissionsTable,{submissions:records,detailStyle:true,problemOrder:['A']}));
  const languages=[...html.matchAll(/<td class="language-value">([^<]+)<\/td>/g)].map(match=>match[1]);
  assert.deepEqual(languages,['c','cpp','py','java','other','other']);
  assert.equal(JSON.stringify(records),before);
  assert(!html.includes('GNU G++20'));assert(!html.includes('Rust 1.75'));
});
