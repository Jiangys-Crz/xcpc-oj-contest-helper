export type SubmissionLanguage='c'|'cpp'|'py'|'java'|'other';

/** Group native compiler labels; a missing label never implies a source language. */
export function submissionLanguage(raw:unknown):SubmissionLanguage {
  if(typeof raw!=='string' || raw.length>200)return 'other';
  const value=raw.normalize('NFKC').trim().toLowerCase();
  if(!value)return 'other';
  if(/(?:^|[\s(])(?:c\+\+|g\+\+|clang\+\+|cpp)(?=$|[\d\s.(+-])/.test(value) || /^cc\./.test(value))return 'cpp';
  if(/(?:^|[\s(])(?:python|pypy)(?=$|[\d\s.(+-])/.test(value) || /^py(?:$|[.\d_-])/.test(value))return 'py';
  if(/(?:^|[\s(])java(?=$|[\d\s.(_-])/.test(value))return 'java';
  if(/(?:^|[\s(])c(?:\d{2})?(?=$|[\s.(_-])/.test(value) || /(?:^|[\s(])gcc(?=$|[\d\s.(+-])/.test(value))return 'c';
  return 'other';
}
