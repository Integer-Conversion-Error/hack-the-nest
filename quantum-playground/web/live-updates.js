/* Debounce edits, serialize work, and never apply a response for superseded inputs. */
(function(root){
  function createLatestUpdater({execute,onResult,onError,onBusy=()=>{}}){
    let revision=0, desired=null, active=false, ready=false, timer=null;
    async function drain(){
      if(active||!ready)return;
      const current=desired;
      ready=false;active=true;onBusy(true);
      try{
        const data=await execute(current.payload);
        if(current.revision===revision)onResult(data,current.meta);
      }catch(error){
        if(current.revision===revision)onError(error,current.meta);
      }finally{
        active=false;onBusy(false);
        if(ready)drain();
      }
    }
    return {
      schedule(payload,meta={},delay=0){
        desired={payload,meta,revision:++revision};
        clearTimeout(timer);ready=false;
        if(delay){timer=setTimeout(()=>{ready=true;drain();},delay);}
        else{ready=true;drain();}
      },
      cancel(){revision++;clearTimeout(timer);ready=false;},
    };
  }
  if(typeof module!=='undefined')module.exports={createLatestUpdater};else root.createLatestUpdater=createLatestUpdater;
})(typeof globalThis!=='undefined'?globalThis:this);
