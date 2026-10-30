import type { NextConfig } from 'next';
const config: NextConfig = { output:'standalone', poweredByHeader:false, serverExternalPackages:['node:sqlite'], experimental:{cpus:1,workerThreads:false} };
export default config;
