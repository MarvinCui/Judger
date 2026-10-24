const {chromium}=require(process.argv[4]);
const assert=require('node:assert/strict');
const readline=require('node:readline');
(async()=>{
 const fs=require('node:fs'), chrome=process.env.JUDGER_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
 const browser=await chromium.launch({headless:true,...(fs.existsSync(chrome)?{executablePath:chrome}:{})});
 const lines=readline.createInterface({input:process.stdin});
 try{
  const page=await browser.newPage({viewport:{width:1180,height:940},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(process.argv[2]);await page.locator('h1').waitFor();
  const original=await page.locator('html').evaluate(e=>getComputedStyle(e).filter);
  assert.equal(await page.locator('iframe, input, textarea').count(),0);
  assert.equal(await page.getByText('Live controls',{exact:false}).count(),0);
  console.log(JSON.stringify({step:'ready',original}));
  for await(const line of lines){
   const message=JSON.parse(line);
   if(message.step==='color'){
    await page.waitForFunction(()=>getComputedStyle(document.documentElement).filter==='saturate(1.2)');
    const colors=await page.locator('[data-trigger-card]').evaluateAll(es=>es.map(e=>getComputedStyle(e).backgroundColor));
    assert.equal(new Set(colors).size,3);
    console.log(JSON.stringify({step:'color',colors}));
   }else if(message.step==='bold'){
    await page.waitForFunction(()=>getComputedStyle(document.querySelector('h1')).fontWeight==='640');
    await page.screenshot({path:process.argv[3],fullPage:true});
    console.log(JSON.stringify({step:'bold',fontWeight:640}));
   }else if(message.step==='undo'){
    await page.waitForFunction(()=>getComputedStyle(document.querySelector('h1')).fontWeight==='600');
    console.log(JSON.stringify({step:'undo'}));
   }else if(message.step==='revise'){
    await page.waitForFunction(()=>getComputedStyle(document.documentElement).filter==='saturate(0.8)');
    console.log(JSON.stringify({step:'revise'}));
   }else if(message.step==='file'){
    await page.getByRole('heading',{name:'A Page Changed from Terminal'}).waitFor();
    assert.equal(await page.getByText('Add more colors?',{exact:true}).count(),0);
    await page.keyboard.press('=');await page.waitForTimeout(300);
    assert.equal(await page.locator('html').evaluate(e=>getComputedStyle(e).filter),'saturate(0.8)');
    await page.setViewportSize({width:500,height:900});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    assert.deepEqual(errors,[]);
    console.log(JSON.stringify({step:'done',passed:true,preview_only:true,auto_reload:true,narrow_layout:true}));
    break;
   }
  }
 }finally{lines.close();await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
