import { handleRequest } from '@/lib/cloud';
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
type Context = { params: Promise<{path:string[]}> };
async function route(request:Request, context:Context) {const {path}=await context.params;return handleRequest(request,path);}
export { route as GET, route as POST, route as PUT, route as PATCH, route as DELETE };
