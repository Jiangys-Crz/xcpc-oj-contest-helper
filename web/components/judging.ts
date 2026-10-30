export type JudgingState='accepted'|'rejected'|'pending'|'ignored';
const accepted=new Set(['CORRECT','AC','OK','ACCEPTED','PRETESTS_PASSED']);
const ignored=new Set(['IGNORED','HACK_SUCCESSFUL','HACK_UNSUCCESSFUL','TOO_LATE']);
const incorrect=new Set(['WRONG_ANSWER','WA','PARTIAL','RUN_ERROR','RUNTIME_ERROR','RE','MEMORY_LIMIT','MEMORY_LIMIT_EXCEEDED','MLE','ML','COMPILER_ERROR','COMPILATION_ERROR','COMPILE_ERROR','CE','TIMELIMIT','TIME_LIMIT_EXCEEDED','TLE','TL','OUTPUT_LIMIT','OUTPUT_LIMIT_EXCEEDED','OLE','OL','NO_OUTPUT','IDLENESS_LIMIT','IDLENESS_LIMIT_EXCEEDED','ILE','FORMAT_ERROR','PRESENTATION_ERROR','PE','SECURITY_VIOLATED','CHALLENGED','HACKED']);

// Unknown and infrastructure results stay unresolved instead of becoming scored failures.
export function judgingState(verdict:string):JudgingState{
  const value=verdict.trim().toUpperCase().replace(/[\s-]+/g,'_');
  if(accepted.has(value))return 'accepted';
  if(ignored.has(value))return 'ignored';
  if(incorrect.has(value))return 'rejected';
  return 'pending';
}
