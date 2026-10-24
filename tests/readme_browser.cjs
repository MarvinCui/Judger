const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {pathToFileURL}=require('node:url');
const {chromium}=require(process.argv[4]);
(async()=>{
 const {marked}=await import(pathToFileURL(path.join(process.argv[5],'lib/marked.esm.js')).href);
 const root=process.argv[2],out=process.argv[3],base=process.argv[6];
 const chrome=process.env.JUDGER_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 const browser=await chromium.launch({headless:true,...(fs.existsSync(chrome)?{executablePath:chrome}:{})});
 try{
  const page=await browser.newPage({viewport:{width:1600,height:560},deviceScaleFactor:1});
  await page.goto(base+'assets/judger-banner.svg');
  await page.screenshot({path:path.join(root,'assets/judger-banner.png')});
  const css=`*{box-sizing:border-box}body{margin:0;background:#fff;color:#1f2328;font:16px/1.6 -apple-system,BlinkMacSystemFont,Arial,sans-serif}article{max-width:1060px;padding:40px 48px;margin:auto}img{max-width:100%;height:auto}a{color:#0969da;text-decoration:none}h2{margin-top:32px;padding-bottom:8px;border-bottom:1px solid #d1d9e0}h3{margin-top:24px}p,table,pre,blockquote{margin:16px 0}table{border-collapse:collapse;display:block;overflow:auto;max-width:100%}td,th{padding:12px;border:1px solid #d1d9e0}th{background:#f6f8fa}pre{padding:18px;background:#f6f8fa;overflow:auto;border-radius:8px;font:13px/1.6 ui-monospace,monospace}code{font-family:ui-monospace,monospace}blockquote{padding:8px 16px;border-left:4px solid #b9c7a3;color:#53604a}hr{border:0;border-top:1px solid #d1d9e0;margin:36px 0}@media(max-width:600px){article{padding:18px 16px}body{font-size:14px}}`;
  for(const file of ['README.md','README.zh-CN.md']){
   await page.goto(base);
   await page.setViewportSize({width:1200,height:900});
   await page.setContent('<!doctype html><meta charset="utf-8"><base href="'+base+'"><style>'+css+'</style><article>'+marked.parse(fs.readFileSync(path.join(root,file),'utf8'))+'</article>');
   await page.waitForFunction(()=>[...document.images].every(i=>i.complete&&i.naturalWidth>0));
   assert.equal(await page.locator('article').innerText().then(t=>t.includes('MIT')),true);
   await page.screenshot({path:path.join(out,file.replace('.md','.png')),fullPage:true});
   await page.setViewportSize({width:430,height:900});
   await page.waitForTimeout(150);
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   console.log(JSON.stringify({file,images:true,narrow_layout:true}));
  }
  console.log('PASS: English and Chinese README render; banner PNG exported.');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
