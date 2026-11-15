process.chdir(require('node:path').resolve(__dirname, '..'));
const assert=require('node:assert/strict');
const {createLatestUpdater}=require('../web/live-updates.js');
const flush=()=>new Promise(resolve=>setImmediate(resolve));
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function main(){
  const calls=[],results=[],errors=[];
  const updater=createLatestUpdater({execute:payload=>new Promise((resolve,reject)=>calls.push({payload,resolve,reject})),onResult:data=>results.push(data),onError:e=>errors.push(e.message)});
  updater.schedule('first');
  updater.schedule('second');
  updater.schedule('latest');
  assert.equal(calls.length,1);
  calls[0].resolve('stale');await flush();
  assert.deepEqual(results,[]);
  assert.equal(calls.length,2);assert.equal(calls[1].payload,'latest');
  calls[1].resolve('fresh');await flush();
  assert.deepEqual(results,['fresh']);
  updater.schedule('discard');updater.cancel();calls[2].resolve('discarded');await flush();
  assert.deepEqual(results,['fresh']);
  updater.schedule('retry');calls[3].reject(Error('network failure'));await flush();
  assert.deepEqual(errors,['network failure']);
  updater.schedule('old',{},15);updater.schedule('new',{},15);await delay(30);
  assert.equal(calls.length,5);assert.equal(calls[4].payload,'new');
  calls[4].resolve('debounced');await flush();
  assert.deepEqual(results,['fresh','debounced']);
  console.log('PASS: slider debounce, serialized requests, latest result, cancellation, and error recovery');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
