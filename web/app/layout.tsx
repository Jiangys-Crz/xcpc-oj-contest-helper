import type { Metadata } from 'next';
import './globals.css';
export const metadata:Metadata={title:{default:'CWNU XCPC Vp System',template:'%s · CWNU XCPC Vp System'},description:'西华师范大学程序设计协会 XCPC 多人组队虚拟参赛平台',robots:{index:false,follow:false}};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="zh-CN"><body>{children}</body></html>}
